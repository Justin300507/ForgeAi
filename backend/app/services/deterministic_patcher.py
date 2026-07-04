"""
Deterministic post-generation patcher.

Runs immediately after files are written, before the validation/runtime loop.
Fixes known LLM failure patterns that the contract can't fully prevent:

  1. passlib → bcrypt  (passlib breaks on bcrypt 4+ / Python 3.13)
  2. FK table imports missing from main.py  (NoReferencedTableError at startup)
  3. async def with sync SQLAlchemy calls  (RuntimeError: no running event loop)
  4. requirements.txt: ensure bcrypt present, remove passlib
  5. jwt_utils / jose → app.utils.auth  (LLMs invent non-existent auth modules)

All fixes are regex/AST-free pattern matching — deterministic, fast, no LLM cost.
"""
import keyword
import re
from pathlib import Path


# Classes always defined inline by the injected app/routes/auth_routes.py
# template (_AUTH_ROUTES_TEMPLATE below) — never imported from app/schemas/.
# Shared by _patch_create_missing_schemas (don't stub these over) and
# duplicate_class_validator (don't flag these as a real conflict).
AUTH_DEFINED_CLASSES = {
    "SignupRequest", "LoginRequest", "TokenResponse", "TokenData",
    "AuthResponse", "RegisterRequest", "RegisterResponse",
}


# ── 0. wrong auth module → app.utils.auth ────────────────────────────────────

# LLMs sometimes invent app.utils.jwt_utils, app.utils.jose, app.utils.security etc.
# Rewrite them all to the known-good app.utils.auth module.
_WRONG_AUTH_MODULE = re.compile(
    r'from app\.utils\.(jwt_utils|jose|security|auth_utils|jwt|token_utils)\s+import\s+([^\n]+)',
    re.MULTILINE,
)


def _patch_wrong_auth_module(content: str) -> str:
    if not _WRONG_AUTH_MODULE.search(content):
        return content
    return _WRONG_AUTH_MODULE.sub(r'from app.utils.auth import \2', content)


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


def _build_model_index(models_dir: Path) -> dict:
    """Index every model class -> {module, tablename, fks:{ref_table: column}}.
    Used to turn a stripped relationship('Target') into a real scoped query."""
    index: dict[str, dict] = {}
    for mf in models_dir.glob("*.py"):
        if mf.name.startswith("_"):
            continue
        try:
            src = mf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        module = mf.stem
        # split into class blocks
        for cm in re.finditer(r"^class\s+(\w+)\s*\(", src, re.MULTILINE):
            cls = cm.group(1)
            start = cm.start()
            nxt = re.search(r"^class\s+\w+\s*\(", src[cm.end():], re.MULTILINE)
            body = src[start: cm.end() + nxt.start()] if nxt else src[start:]
            tbl_m = re.search(r"__tablename__\s*=\s*['\"](\w+)['\"]", body)
            fks: dict[str, str] = {}
            for fkm in re.finditer(r"^\s*(\w+)\s*=\s*Column\([^\n]*ForeignKey\(\s*['\"](\w+)\.", body, re.MULTILINE):
                col, ref_table = fkm.group(1), fkm.group(2)
                fks.setdefault(ref_table, col)
            index[cls] = {"module": module, "table": tbl_m.group(1) if tbl_m else None, "fks": fks}
    return index


def _inject_relationship_property(content: str, owning_class: str, attr: str,
                                  target: str, model_index: dict) -> str:
    """Insert a @property named `attr` into `owning_class` that returns the
    related row(s) via the object's session (empty list / None if unresolvable).

    Two distinct relationship directions, and getting this backwards is worse
    than not injecting anything:

    - Many-to-one ("belongs to"): the OWNING model itself holds the FK column
      pointing at the target's table (e.g. Budget.category_id -> categories.id).
      Must check this FIRST. The target has no FK back to the owner in this
      direction, so the one-to-many branch below finds nothing and silently
      degrades to an always-empty list -- for a field like `budget.category`
      that a response schema types as a single object/str, serializing `[]`
      against it is a 500 on every request that returns a non-null row
      (reproduced live: Budget.category stripped from a relationship() and
      re-injected as a list-returning property, crashing GET /budgets and
      POST /budgets the moment any budget exists).
    - One-to-many: the TARGET model holds the FK back to the owning table
      (e.g. HabitCompletion.habit_id -> habits.id, for Habit.completions).
    """
    my_table = model_index.get(owning_class, {}).get("table")
    tgt = model_index.get(target)
    tgt_table = tgt.get("table") if tgt else None
    owning_fks = model_index.get(owning_class, {}).get("fks", {})
    own_fk_col = owning_fks.get(tgt_table) if tgt_table else None
    fk_col = tgt["fks"].get(my_table) if (tgt and my_table) else None

    if tgt and own_fk_col:
        body = (
            f"    @property\n"
            f"    def {attr}(self):\n"
            f"        from sqlalchemy import inspect as _sa_inspect\n"
            f"        _sess = _sa_inspect(self).session\n"
            f"        if _sess is None or self.{own_fk_col} is None:\n"
            f"            return None\n"
            f"        from app.models.{tgt['module']} import {target}\n"
            f"        return _sess.query({target}).get(self.{own_fk_col})\n"
        )
    elif tgt and fk_col:
        body = (
            f"    @property\n"
            f"    def {attr}(self):\n"
            f"        from sqlalchemy import inspect as _sa_inspect\n"
            f"        _sess = _sa_inspect(self).session\n"
            f"        if _sess is None:\n"
            f"            return []\n"
            f"        from app.models.{tgt['module']} import {target}\n"
            f"        return _sess.query({target}).filter({target}.{fk_col} == self.id).all()\n"
        )
    else:
        # Can't resolve the target/FK — degrade to empty list so the accessor
        # never raises AttributeError (better a missing list than a 500).
        body = (
            f"    @property\n"
            f"    def {attr}(self):\n"
            f"        return []\n"
        )

    # Insert after the last "    col = Column(...)" line in the owning class, or
    # right after the class header if there are none.
    start_m = re.search(rf"^class\s+{owning_class}\s*\([^\n]*\)\s*:", content, re.MULTILINE)
    if not start_m:
        return content
    nxt = re.search(r"^class\s+\w+\s*\(", content[start_m.end():], re.MULTILINE)
    class_end = start_m.end() + nxt.start() if nxt else len(content)
    class_body = content[start_m.end():class_end]

    col_matches = list(re.finditer(r"^\s{4}\w+\s*=\s*Column\([^\n]*\n", class_body, re.MULTILINE))
    if col_matches:
        insert_at = start_m.end() + col_matches[-1].end()
    else:
        nl = content.index("\n", start_m.end())
        insert_at = nl + 1
    return content[:insert_at] + "\n" + body + content[insert_at:]


def _patch_strip_relationships(project_path: Path) -> int:
    """
    Strip ALL relationship() attribute declarations from SQLAlchemy model files.

    Even after stripping back_populates/backref, the base relationship("Target")
    call remains — and if there is no FK path between the two tables (common with
    many-to-many schemas where the LLM forgets secondary=), SQLAlchemy raises
    NoForeignKeysError at mapper config time, which hangs every single endpoint.

    Removing the mapper-level relationship is necessary, but route handlers DO
    sometimes use the accessor (e.g. `any(c.completion_date == today for c in
    habit.completions)`), so a blanket strip turns those into
    AttributeError -> 500. To keep both properties (no mapper crash AND the
    accessor still works), each stripped relationship is replaced with a plain
    Python @property that queries the related rows through the object's own
    session, scoped by the target's foreign key back to this table. Falls back to
    an empty list when the target/FK can't be resolved.
    """
    models_dir = project_path / "app" / "models"
    if not models_dir.exists():
        return 0

    model_index = _build_model_index(models_dir)

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

        # Remove relationship() assignments; track which class each belonged to
        # and the target it referenced so we can re-add a query property.
        lines = original.split("\n")
        new_lines: list[str] = []
        i = 0
        removed = 0
        current_class: str | None = None
        # {owning_class: [(attr, target_class)]}
        stripped_rels: dict[str, list[tuple[str, str]]] = {}
        while i < len(lines):
            line = lines[i]
            cls_m = re.match(r'class\s+(\w+)\s*\(', line)
            if cls_m:
                current_class = cls_m.group(1)
            stripped = line.lstrip()
            rel_m = re.match(r'(\w+)\s*=\s*relationship\s*\(\s*[\'"]?(\w+)', stripped)
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
                if rel_m and current_class:
                    stripped_rels.setdefault(current_class, []).append(
                        (rel_m.group(1), rel_m.group(2))
                    )
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

        # Re-add each stripped relationship as a session-backed query property so
        # `obj.<attr>` keeps working in route handlers.
        for owning_class, rels in stripped_rels.items():
            for attr, target in rels:
                content = _inject_relationship_property(
                    content, owning_class, attr, target, model_index
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
# Match both `@router.` (before rename) and `@task_router.` (after rename)
_ROUTER_DECORATOR = re.compile(r"^@(?:\w+_router|router)\.(get|post|put|delete|patch)\b", re.MULTILINE)
_SYNC_ORM = re.compile(r"\bdb\.(query|add|commit|delete|refresh|execute|flush|rollback)\b")
_DB_DEPENDS = re.compile(r"\bdb\s*:\s*Session\b")
_AWAIT_USAGE = re.compile(r"\bawait\b")


def _patch_async_sync(content: str, filepath: str = "") -> str:
    if "async def" not in content:
        return content

    norm = filepath.replace("\\", "/")
    # Route files: any async def without a real await blocks uvicorn's event loop
    is_route_file = "/routes/" in norm or norm.endswith("_routes.py")

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
            has_db_depends = _DB_DEPENDS.search(line)

            # In route files: strip async from ALL handlers that don't use real await.
            # Any sync I/O (db.query, file I/O, etc.) inside async def blocks the uvicorn
            # event loop and causes all subsequent requests to timeout.
            # Outside route files: be conservative — only strip when we're sure it's wrong.
            if is_route_file:
                should_strip = not has_real_await
            else:
                should_strip = has_sync_orm or has_db_depends or (is_route_handler and not has_real_await)

            if should_strip:
                body_text = body_text.replace("async def ", "def ", 1)
                result.append(body_text)
                i = j
                continue
        result.append(line)
        i += 1

    return "".join(result)


# ── 3b. Circular import prevention in schema files ────────────────────────────
# Schema files importing from route files creates an import cycle that crashes
# at startup: schemas import routes, routes import schemas.

def _patch_circular_schema_imports(content: str, filepath: str = "") -> str:
    """Remove any 'from app.routes.*' imports from schema files to break circular deps."""
    norm = filepath.replace("\\", "/")
    if "/schemas/" not in norm:
        return content
    if "from app.routes." not in content:
        return content
    patched = re.sub(r"from app\.routes\.\w+ import [^\n]+\n?", "", content)
    if patched != content:
        print(f"  [patcher] Removed circular route import from schema file: {norm.split('/')[-1]}")
    return patched


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
                name = raw.strip().split("#")[0].strip().split(" as ")[0].strip()
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
        elif names - defined:
            # No suitable alias found. If the file has no class at all (e.g. uses Table()),
            # inject stub Base classes for each missing name so the import doesn't fail.
            if not defined:
                # Collect names already available via import (don't stub those)
                already_imported: set[str] = set()
                for m in re.finditer(r'^from\s+\S+\s+import\s+([^\n]+)', content, re.MULTILINE):
                    for n in m.group(1).split(","):
                        already_imported.add(n.strip().split(" as ")[-1].strip())
                for m in re.finditer(r'^import\s+([^\n]+)', content, re.MULTILINE):
                    for n in m.group(1).split(","):
                        already_imported.add(n.strip().split(" as ")[-1].strip())

                # Before stubbing, look for the REAL model in a sibling module
                # (user.py needing User while users.py defines Users). An empty
                # stub shadows the real model: auth then queries the stub and
                # crashes with "type object 'User' has no attribute 'email'".
                # A re-export shim gives routes the actual class instead.
                sibling_classes: dict[str, str] = {}  # class name -> module
                for sibling in models_dir.glob("*.py"):
                    if sibling.stem in (module, "__init__"):
                        continue
                    try:
                        sib_content = sibling.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        continue
                    for cls in _CLASS_IN_FILE.findall(sib_content):
                        sibling_classes.setdefault(cls, sibling.stem)

                stubs = []
                reexports = []
                for name in sorted(names):
                    if name in already_imported:
                        continue  # already importable — don't create a duplicate stub
                    name_lower = name.lower()
                    real = None
                    for cls in sorted(sibling_classes):
                        c = cls.lower()
                        if (c == name_lower or c == name_lower + "s"
                                or c + "s" == name_lower
                                or (c.rstrip("s") == name_lower.rstrip("s")
                                    and abs(len(c) - len(name_lower)) <= 2)):
                            real = cls
                            break
                    if real:
                        src_module = sibling_classes[real]
                        as_clause = f" as {name}" if real != name else ""
                        reexports.append(
                            f"from app.models.{src_module} import {real}{as_clause}  # re-export: patcher\n"
                        )
                        continue
                    # Derive a table name from the module name (e.g. task_tags)
                    tbl = module.replace("-", "_")
                    stubs.append(
                        f"\nclass {name}(Base):  # stub: patcher\n"
                        f"    __tablename__ = '{tbl}'\n"
                        f"    id = Column(Integer, primary_key=True)\n"
                    )
                if reexports:
                    content = "".join(reexports) + content
                    model_file.write_text(content, encoding="utf-8")
                    print(f"  [patcher] Re-exported real model(s) in {module}.py: "
                          f"{[r.strip() for r in reexports]}")
                if stubs:
                    # Ensure imports exist
                    needs = []
                    if "Column" not in content:
                        needs.append("from sqlalchemy import Column, Integer")
                    if "from app.database import Base" not in content:
                        needs.append("from app.database import Base")
                    prefix = "\n".join(needs) + "\n" if needs else ""
                    content = prefix + content.rstrip() + "\n" + "".join(stubs)
                    model_file.write_text(content, encoding="utf-8")
                    print(f"  [patcher] Added stub class(es) in {module}.py: {sorted(names - defined)}")


def _dedupe_class_files(target_dir: Path, kind: str) -> int:
    """
    When both user.py and users.py (or expense.py and expenses.py) exist in the
    same directory and both define the same class name, keep the file with more
    content and delete the other. Any import still pointing at the dropped
    file's module path is left for _patch_redirect_missing_backend_imports to
    resolve (it runs later and indexes symbols across the whole app/ tree).

    `kind` is just the noun used in the log line ("model" / "schema").
    """
    if not target_dir.exists():
        return 0

    removed = 0
    py_files = {f.stem: f for f in target_dir.glob("*.py") if not f.name.startswith("_")}

    # Look for singular/plural pairs, e.g. user + users
    checked: set[str] = set()
    for stem, fpath in list(py_files.items()):
        if stem in checked:
            continue
        # Check if plural/singular counterpart exists
        partner = stem.rstrip("s") if stem.endswith("s") else stem + "s"
        if partner not in py_files or partner in checked:
            continue
        checked.add(stem)
        checked.add(partner)

        f1, f2 = fpath, py_files[partner]
        try:
            c1 = f1.read_text(encoding="utf-8", errors="ignore")
            c2 = f2.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        classes1 = set(re.findall(r"^class (\w+)\s*\(", c1, re.MULTILINE))
        classes2 = set(re.findall(r"^class (\w+)\s*\(", c2, re.MULTILINE))
        shared = classes1 & classes2
        if not shared:
            continue

        # Keep the file with more content; delete the other, add alias
        keep, drop = (f1, f2) if len(c1) >= len(c2) else (f2, f1)
        keep_content = keep.read_text(encoding="utf-8", errors="ignore")
        for cls in sorted(shared):
            # Add alias for the drop-file's stem (so both imports resolve)
            alias_line = f"{cls} = {cls}  # deduplicated from {drop.stem}"
            if alias_line not in keep_content and f"\n{cls} = " not in keep_content:
                keep_content = keep_content.rstrip() + f"\n# Removed duplicate {drop.name}\n"
                break
        keep.write_text(keep_content, encoding="utf-8")
        drop.unlink()
        print(f"  [patcher] Removed duplicate {kind} {drop.name} (kept {keep.name}), shared classes: {shared}")
        removed += 1

    return removed


def _patch_deduplicate_models(project_path: Path) -> int:
    return _dedupe_class_files(project_path / "app" / "models", "model")


def _patch_deduplicate_schemas(project_path: Path) -> int:
    """
    Same singular/plural collision as models (e.g. app/schemas/expense.py vs
    app/schemas/expenses.py both defining ExpenseBase/ExpenseCreate/...), but
    for Pydantic schemas -- unlike models this never crashes at import time,
    so it doesn't force a fix loop revert, but it IS a genuine correctness bug
    (route A imports ExpenseCreate from expense.py while route B imports the
    same-named-but-different class from expenses.py) and the static analyzer
    flags it as "Duplicate class definition" every single pass, permanently
    reoccupying one diagnostic slot in the fix loop's per-attempt budget.
    Seen live stalling forge_finance's fix loop across all 3 attempts.
    """
    return _dedupe_class_files(project_path / "app" / "schemas", "schema")


# ── 5. response_model using SQLAlchemy model instead of Pydantic schema ──────

_FROM_MODELS_IMPORT = re.compile(r"^from app\.models\.\w+ import ([\w,\s]+)", re.MULTILINE)
_RESPONSE_MODEL_ATTR = re.compile(r"\bresponse_model\s*=\s*(List\[)?(\w+)(])?")


def _patch_orm_response_model(content: str, filepath: str, project_path: Path = None) -> str:
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

    # Build a map of orm_class_name -> (schema_class_name, schema_module) from app/schemas/
    # e.g. "User" -> ("UserResponse", "app.schemas.user") or ("UserSchema", "app.schemas.user")
    schema_map: dict[str, tuple[str, str]] = {}
    if project_path:
        schemas_dir = project_path / "app" / "schemas"
        if schemas_dir.exists():
            for sf in schemas_dir.glob("*.py"):
                if sf.name.startswith("_"):
                    continue
                try:
                    sc = sf.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                module_name = f"app.schemas.{sf.stem}"
                for cls_m in re.finditer(r"^class (\w+)\s*\(.*BaseModel.*\)", sc, re.MULTILINE):
                    schema_cls = cls_m.group(1)
                    # Match: UserResponse -> User, UserSchema -> User, UserBase -> User, etc.
                    for orm_cls in orm_classes:
                        base = orm_cls.rstrip("s")  # "Users" -> "User", "User" -> "User"
                        if schema_cls.startswith(base) and schema_cls != orm_cls:
                            if orm_cls not in schema_map:
                                schema_map[orm_cls] = (schema_cls, module_name)

    def _replace_rm(m: re.Match) -> str:
        prefix = m.group(1) or ""   # "List[" or ""
        cls_name = m.group(2)
        suffix = m.group(3) or ""   # "]" or ""
        if cls_name not in orm_classes:
            return m.group(0)
        if cls_name in schema_map:
            schema_cls, _ = schema_map[cls_name]
            return f"response_model={prefix}{schema_cls}{suffix}"
        return f"response_model={prefix}dict{suffix}"  # fallback: dict serializes fine

    new_content = _RESPONSE_MODEL_ATTR.sub(_replace_rm, content)

    # Add imports for any schema classes we substituted in
    for orm_cls, (schema_cls, module_name) in schema_map.items():
        if orm_cls in orm_classes and schema_cls in new_content:
            import_line = f"from {module_name} import {schema_cls}"
            if import_line not in new_content:
                # Insert after the last "from app." import line
                last_import_end = 0
                for im in re.finditer(r"^from app\.[^\n]+\n", new_content, re.MULTILINE):
                    last_import_end = im.end()
                if last_import_end:
                    new_content = new_content[:last_import_end] + import_line + "\n" + new_content[last_import_end:]
                else:
                    new_content = import_line + "\n" + new_content

    return new_content


# ── 5. Pydantic v2: regex= → pattern= in Field() calls ──────────────────────

def _patch_pydantic_regex(content: str) -> str:
    """Pydantic v2 removed `regex=` kwarg from Field() — replace with `pattern=`."""
    if "regex=" not in content:
        return content
    return re.sub(r"\bregex\s*=\s*(r?['\"])", r"pattern=\1", content)


# ── 5b. SQLAlchemy func().name(...) → func().label(...) ─────────────────────
# LLMs regularly confuse the read-only `.name` attribute on a Function element
# (holds the SQL function's own name, e.g. "count") with `.label()`, the method
# for aliasing a query column. `func.count(x).name("y")` calls a string as if
# it were a function — `TypeError: 'str' object is not callable` — which
# raises on every request that touches the query and surfaces as a bare 500.

_FUNC_CALL_RE = re.compile(r"func\.\w+\(")


def _patch_func_name_vs_label(content: str) -> str:
    if "func." not in content or ".name(" not in content:
        return content

    replacements = []
    n = len(content)
    for m in _FUNC_CALL_RE.finditer(content):
        depth = 1
        j = m.end()
        while j < n and depth > 0:
            if content[j] == "(":
                depth += 1
            elif content[j] == ")":
                depth -= 1
            j += 1
        if content[j:j + 6] == ".name(":
            replacements.append((j, j + 5))  # span of ".name" (keep the "(")

    if not replacements:
        return content
    patched = content
    for start, end in sorted(replacements, key=lambda t: -t[0]):
        patched = patched[:start] + ".label" + patched[end:]
    return patched


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
            continue  # already correctly named (e.g. auth_router in auth_routes.py) — leave it

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


def authenticate_user(db: Session, email: str, password: str):
    """Convenience helper — LLM-generated routes commonly import this."""
    from app.models.user import User
    user = db.query(User).filter(User.email == email).first()
    if not user:
        return None
    for field in ("hashed_password", "password_hash", "password"):
        stored = getattr(user, field, None)
        if stored and verify_password(password, stored):
            return user
    return None
'''


# ── 9b. Known-good Pagination.jsx injection ──────────────────────────────────
# LLMs (especially fallback models used whenever Gemini 503s) routinely
# mis-close braces in this component -- a ternary nested inside a template
# literal nested inside a JSX attribute expression, the same failure class
# documented for Toast/NavLink -- producing an unparseable
# `Expected "}" but found "currentPage"` esbuild error that fails the whole
# Vite build. Seen live across unrelated generations (habit tracker, expense
# tracker) with the exact same prop signature every time. Pagination has no
# business logic or domain-specific content, so -- like auth_routes.py and
# database.py -- it's safe to always normalize to a known-good static
# implementation rather than repair whatever shape the LLM produced.

_PAGINATION_TEMPLATE = '''\
import React from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';

const Pagination = ({ currentPage, totalPages, onPageChange }) => {
  if (!totalPages || totalPages <= 1) return null;

  return (
    <div className="flex items-center justify-center gap-2 mt-6">
      <button
        type="button"
        onClick={() => onPageChange(currentPage - 1)}
        disabled={currentPage <= 1}
        className="p-2 rounded-lg text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-700 disabled:opacity-50 disabled:cursor-not-allowed"
      >
        <ChevronLeft size={18} />
      </button>
      <span className="text-sm font-medium text-slate-700 dark:text-slate-300">
        Page {currentPage} of {totalPages}
      </span>
      <button
        type="button"
        onClick={() => onPageChange(currentPage + 1)}
        disabled={currentPage >= totalPages}
        className="p-2 rounded-lg text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-700 disabled:opacity-50 disabled:cursor-not-allowed"
      >
        <ChevronRight size={18} />
      </button>
    </div>
  );
};

export default Pagination;
'''


def _patch_pagination_component(project_path: Path) -> bool:
    pagination_file = project_path / "src" / "components" / "Pagination.jsx"
    if not pagination_file.exists():
        return False
    try:
        content = pagination_file.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return False
    if "currentPage" not in content:
        return False  # not actually the standard pagination component -- leave it alone
    if content == _PAGINATION_TEMPLATE:
        return False
    pagination_file.write_text(_PAGINATION_TEMPLATE, encoding="utf-8")
    print("  [patcher] Injected known-good src/components/Pagination.jsx")
    return True


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
    # Fill any remaining NOT NULL columns that have no default so injected auth
    # routes work even when the LLM adds custom non-nullable columns (e.g. status).
    _COL_STR_DEFAULTS = {
        "status": "active", "state": "active", "type": "user",
        "gender": "other", "plan": "free", "tier": "basic",
        "account_type": "standard", "subscription": "free",
    }
    for col in User.__table__.columns:
        if col.name in kw or col.primary_key:
            continue
        if col.nullable or col.default is not None or col.server_default is not None:
            continue
        col_type = type(col.type).__name__.lower()
        if col.name in _COL_STR_DEFAULTS:
            kw[col.name] = _COL_STR_DEFAULTS[col.name]
        elif any(t in col_type for t in ("str", "text", "char", "varchar")):
            kw[col.name] = "active"
        elif any(t in col_type for t in ("int", "float", "numeric", "decimal")):
            kw[col.name] = 0
        elif "bool" in col_type:
            kw[col.name] = True
        else:
            kw[col.name] = ""
    return User(**kw)


def _read_password(user) -> str | None:
    for field in ("hashed_password", "password_hash", "password"):
        val = getattr(user, field, None)
        if val:
            return val
    return None


@auth_router.post("/auth/signup")
@auth_router.post("/auth/register")
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


@auth_router.post("/auth/logout")
def logout(current_user=Depends(get_current_user)):
    # JWTs are stateless — logout is handled client-side by discarding the token.
    # This endpoint confirms the user is authenticated and acknowledges the request.
    return {"message": "Successfully logged out"}
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
    # Real datetime types, NOT str: the ORM hands pydantic datetime objects,
    # and a str annotation raised ResponseValidationError (500) on every
    # request that returned a row with created_at/updated_at.
    "DateTime": "datetime", "Date": "date", "Time": "time",
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

    # Classes defined in our injected auth_routes.py — never create stubs for these,
    # as a stub with 'pass' body causes AttributeError when the route accesses .email/.password.
    _AUTH_DEFINED_CLASSES = AUTH_DEFINED_CLASSES

    # A class already defined in ANY schema file must never get a second,
    # incompatible stub definition elsewhere. Without this, if a route imports
    # `from app.schemas.workout import ExerciseCreate` while ExerciseCreate
    # actually (and correctly) lives in app/schemas/exercise.py, this patcher
    # used to see it "missing" from workout.py and create a duplicate --
    # directly recreating "Duplicate class definition" errors that a prior fix
    # attempt had just resolved by removing the duplicate from workout.py.
    class_owner: dict[str, str] = {}
    for sf in schemas_dir.glob("*.py"):
        try:
            sf_content = sf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for cls_name in re.findall(r"^class (\w+)\s*\(", sf_content, re.MULTILINE):
            class_owner.setdefault(cls_name, sf.stem)

    created = 0
    for module, needed_classes in schema_imports.items():
        # Filter out auth-defined classes before processing
        needed_classes = needed_classes - _AUTH_DEFINED_CLASSES
        already_elsewhere = {n for n in needed_classes if class_owner.get(n) not in (None, module)}
        if already_elsewhere:
            print(f"  [patcher] Skipping stub for {sorted(already_elsewhere)} in {module}.py "
                  f"— already defined in {sorted({class_owner[n] for n in already_elsewhere})}.py "
                  f"(import path may need fixing, but no duplicate stub created)")
            needed_classes = needed_classes - already_elsewhere
        if not needed_classes:
            continue
        schema_file = schemas_dir / f"{module}.py"

        # Try to find the matching model file (handle list_member / listmember)
        model_file: Path | None = None
        candidates = [module, module.replace("_", ""), module + "s", module.rstrip("s")]
        for mf in models_dir.glob("*.py"):
            if mf.stem in candidates or mf.stem.replace("_", "") in candidates:
                model_file = mf
                break

        # Build field lines from model columns. id MUST be included: these
        # stubs are used as response_model, and a schema without id serializes
        # every response with the id stripped — the CRUD journey then can't
        # capture an entity_id and edit/delete/persistence all fail.
        columns: list[tuple[str, str]] = []
        if model_file and model_file.exists():
            model_text = model_file.read_text(encoding="utf-8", errors="replace")
            for cm in _COL_RE.finditer(model_text):
                col_name = cm.group(1)
                py_type = _TYPE_MAP.get(cm.group(2), "str")
                columns.append((col_name, py_type))
            if not any(n == "id" for n, _ in columns):
                columns.insert(0, ("id", "int"))

        _dt_names = sorted({t for _, t in columns if t in ("datetime", "date", "time")})

        if schema_file.exists():
            # File exists — only add classes that are missing from it
            existing_content = schema_file.read_text(encoding="utf-8", errors="replace")
            existing_classes = set(re.findall(r"^class (\w+)\s*\(", existing_content, re.MULTILINE))
            missing = sorted(
                n for n in (needed_classes - existing_classes - _AUTH_DEFINED_CLASSES)
                if n.isidentifier() and not keyword.iskeyword(n)
            )
            if not missing:
                continue
            additions = []
            if "Optional" not in existing_content:
                additions.append("from typing import Optional\n")
            if _dt_names and "from datetime import" not in existing_content:
                additions.append(f"from datetime import {', '.join(_dt_names)}\n")
            for cls_name in missing:
                if columns:
                    field_lines = "\n".join(
                        f"    {name}: Optional[{typ}] = None" for name, typ in columns
                    )
                else:
                    field_lines = "    pass"
                additions.append(
                    f"\n\nclass {cls_name}(BaseModel):\n{field_lines}\n    model_config = {{'from_attributes': True}}"
                )
            schema_file.write_text(existing_content.rstrip() + "\n" + "".join(additions) + "\n", encoding="utf-8")
            created += 1
            print(f"  [patcher] Added missing class(es) to existing {module}.py: {missing}")
            continue

        base_name = "".join(w.capitalize() for w in module.split("_"))

        valid_classes = sorted(
            n for n in needed_classes
            if n.isidentifier() and not keyword.iskeyword(n)
        )
        if not valid_classes:
            continue
        lines = ["from typing import Optional", "from pydantic import BaseModel"]
        if _dt_names:
            lines.append(f"from datetime import {', '.join(_dt_names)}")
        lines += ["", ""]
        for cls_name in valid_classes:
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
    r'^class (\w+(?:Response|Out|Read|List|Detail|Schema)\w*)\s*\([^)]*\)',
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
            # Detect class declaration. Match ANY base (not just BaseModel) so a
            # response class that inherits from a shared base schema
            # (class HabitResponse(HabitBase)) still gets its fields made
            # Optional[...] = None — otherwise its required fields slip through and
            # a missing/None value on the ORM object 500s with ResponseValidationError.
            # Still gated on the Response/Out/Read/List/Detail/Schema naming, so
            # input schemas (Create/Update) keep their required fields.
            cm = re.match(r'^(class \w+(?:Response|Out|Read|List|Detail|Schema)\w*\s*\([^)]*\))', line, re.IGNORECASE)
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


# ── 14b. Inject from_attributes=True into all Pydantic schemas ──────────────
# FastAPI needs model_config = {'from_attributes': True} in every Pydantic schema
# that is used as a response_model, otherwise it raises PydanticSerializationError
# when trying to serialize SQLAlchemy ORM objects returned from route handlers.

def _patch_schemas_from_attributes(project_path: Path) -> int:
    """Inject model_config = {'from_attributes': True} into every Pydantic BaseModel
    schema class in app/schemas/, app/routes/, and app/services/ that lacks it."""
    app_dir = project_path / "app"
    if not app_dir.exists():
        return 0

    # Scan schemas + routes + services (inline response schemas need from_attributes too)
    scan_dirs = [app_dir / "schemas", app_dir / "routes", app_dir / "services"]

    patched = 0
    for scan_dir in scan_dirs:
        if not scan_dir.exists():
            continue
        for sf in scan_dir.glob("*.py"):
            if sf.name.startswith("_"):
                continue
            try:
                content = sf.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if "BaseModel" not in content:
                continue

            lines = content.split('\n')
            out = list(lines)
            changed = False
            inserted = 0

            for i, line in enumerate(lines):
                if not re.match(r'^class \w+\s*\(.*BaseModel.*\)\s*:', line):
                    continue
                j = i + 1
                body_indent = '    '
                has_config = False
                while j < len(lines):
                    jline = lines[j]
                    stripped = jline.lstrip()
                    if jline and not jline[0].isspace() and stripped and not stripped.startswith('#'):
                        break
                    if 'model_config' in jline or 'from_attributes' in jline:
                        has_config = True
                        break
                    if stripped and body_indent == '    ':
                        m = re.match(r'^(\s+)', jline)
                        if m:
                            body_indent = m.group(1)
                    j += 1

                if not has_config:
                    insert_at = (i + 1) + inserted
                    out.insert(insert_at, f'{body_indent}model_config = {{"from_attributes": True}}')
                    inserted += 1
                    changed = True

            if changed:
                sf.write_text('\n'.join(out), encoding="utf-8")
                patched += 1

    if patched:
        print(f"  [schema_patcher] Added from_attributes=True to {patched} schema file(s)")
    return patched


# ── 15. Pydantic v1 orm_mode → v2 from_attributes ────────────────────────────

_ORM_MODE_CLS_CONFIG_RE = re.compile(
    r'(class Config\s*:\s*\n(?:[ \t]+[^\n]*\n)*?)([ \t]+)orm_mode\s*=\s*True\s*\n',
    re.MULTILINE,
)
_ORM_MODE_ASSIGN_RE = re.compile(r'\borm_mode\s*=\s*True')


def _patch_pydantic_orm_mode(content: str) -> str:
    """Replace Pydantic v1 orm_mode=True with v2 model_config from_attributes."""
    if "orm_mode" not in content:
        return content

    # Replace class Config: orm_mode = True with model_config = ConfigDict(from_attributes=True)
    def _replace_config_class(m: re.Match) -> str:
        header = m.group(1)
        indent = m.group(2)
        # Check if there are other settings in class Config
        remaining = re.sub(r'[ \t]+orm_mode\s*=\s*True\s*\n', '', header)
        # If only orm_mode was there, the class body is now empty — drop the class
        body_lines = [l for l in remaining.split('\n')[1:] if l.strip()]
        if not body_lines:
            return f"{indent}model_config = {{'from_attributes': True}}\n"
        return remaining + f"{indent}model_config = {{'from_attributes': True}}\n"

    content = _ORM_MODE_CLS_CONFIG_RE.sub(_replace_config_class, content)
    # Also catch bare orm_mode = True outside class Config (LLM sometimes puts it at schema level)
    content = _ORM_MODE_ASSIGN_RE.sub("model_config = {'from_attributes': True}", content)
    return content


# ── 16. SQLAlchemy ORM model used as Pydantic field type in route files ───────
# When LLM defines `class TaskOut(BaseModel): labels: List[Label]` in a route
# file and `Label` is imported from `app.models.labels` (SQLAlchemy model),
# Pydantic v2 raises PydanticSchemaGenerationError at startup.
# Fix: replace the ORM class type with `Any` (or `List[Any]`) in these fields.

_PYDANTIC_CLASS_RE = re.compile(
    r'^class (\w+)\s*\(BaseModel\)',
    re.MULTILINE,
)


_STAR_DICT_RE = re.compile(
    r'\b([A-Z]\w+)\(\s*\*\*(\w+)\.(?:dict|model_dump)\(\)((?:[^)]*)?)\)',
    re.DOTALL,
)


def _patch_star_dict_extra_fields(project_path: Path) -> int:
    """Replace Model(**schema.dict(), ...) with filtered dict to prevent
    TypeError when the Pydantic schema has extra fields the SQLAlchemy model lacks.

    Example:
        BEFORE: Task(**task_in.dict(), user_id=current_user.id)
        AFTER:  Task(**{k: v for k, v in task_in.dict().items() if k in Task.__table__.columns.keys()}, user_id=current_user.id)

    Filters against __table__.columns rather than hasattr(): a model that
    exposes a read-only @property with the same name as a schema field (e.g.
    `category` computed from a relationship) still passes hasattr(), so the
    dict comprehension includes it, and the constructor call then raises
    AttributeError: property 'category' of 'Expense' object has no setter --
    a crash on every create request. __table__.columns only ever contains
    actually-assignable mapped columns. Reproduced live on forge_expense_tracker.
    """
    routes_dir = project_path / "app" / "routes"
    models_dir = project_path / "app" / "models"
    if not routes_dir.exists():
        return 0

    # Collect all SQLAlchemy model class names (inherit from Base)
    orm_classes: set = set()
    if models_dir.exists():
        for mf in models_dir.glob("*.py"):
            if mf.name == "__init__.py":
                continue
            try:
                for m in re.finditer(r'^class\s+(\w+)\s*\(', mf.read_text(encoding="utf-8", errors="replace"), re.MULTILINE):
                    orm_classes.add(m.group(1))
            except Exception:
                pass
    # Remove known non-ORM base classes
    orm_classes -= {"Base", "BaseModel", "Config", "Meta"}

    patched = 0
    for rf in routes_dir.glob("*.py"):
        try:
            content = rf.read_text(encoding="utf-8", errors="replace")
            original = content

            def _replace_star_dict(m: re.Match) -> str:
                cls_name = m.group(1)
                schema_var = m.group(2)
                extra_args = m.group(3)  # everything after dict() up to the closing )
                if cls_name not in orm_classes:
                    return m.group(0)
                # Explicit trailing kwargs (e.g. `, user_id=current_user.id`) must
                # also be excluded from the filtered dict -- if the schema itself
                # has a same-named field accepted as client input (a common
                # MassAssignment gap: HabitCreate exposing `user_id`/`id` as
                # optional), Python raises "got multiple values for keyword
                # argument" when both the unpacked dict and the explicit kwarg
                # supply it. Reproduced live: POST /habits 500'd on every request
                # because HabitCreate.user_id passed the column-name filter and
                # collided with the route's own `user_id=current_user.id`.
                explicit_kwargs = sorted(set(re.findall(r'(?:^|,)\s*(\w+)\s*=', extra_args)))
                exclude_clause = ""
                if explicit_kwargs:
                    exclude_set = "{" + ", ".join(f"'{k}'" for k in explicit_kwargs) + "}"
                    exclude_clause = f" and k not in {exclude_set}"
                # Build filtered dict, preserve any extra kwargs
                filtered = f"{{k: v for k, v in {schema_var}.dict().items() if k in {cls_name}.__table__.columns.keys(){exclude_clause}}}"
                if extra_args.strip().strip(",").strip():
                    return f"{cls_name}(**{filtered}{extra_args})"
                return f"{cls_name}(**{filtered})"

            content = _STAR_DICT_RE.sub(_replace_star_dict, content)
            if content != original:
                rf.write_text(content, encoding="utf-8")
                patched += 1
                print(f"  [patcher] Filtered **schema.dict() kwargs in {rf.name}")
        except Exception:
            pass
    return patched


_FILTERED_CTOR_KWARG_COLLISION_RE = re.compile(
    r'\b([A-Z]\w+)\(\*\*\{k: v for k, v in (\w+)\.(?:dict|model_dump)\(\)\.items\(\)'
    r' if k in \1\.__table__\.columns\.keys\(\)\}((?:[^)]*)?)\)',
)


def _patch_filtered_ctor_kwarg_collision(project_path: Path) -> int:
    """
    Fix an already-generated `Model(**{k: v for ... if k in Model.__table__.
    columns.keys()}, some_kwarg=value)` call that's missing the "and k not in
    {...}" exclusion _patch_star_dict_extra_fields now adds for any trailing
    explicit kwarg.

    Without it, if the schema also accepts a same-named field as client input
    (HabitCreate exposing `user_id` as optional -- a MassAssignment gap flagged
    by security review but never auto-fixed), the unpacked dict AND the
    explicit kwarg both supply that name and Python raises "got multiple
    values for keyword argument". Reproduced live: POST /habits 500'd on
    every single request for exactly this reason, and the pipeline's own
    runtime-fix loop failed to resolve it before giving up, shipping the app
    at "deploy ready" with a completely broken create flow.
    """
    routes_dir = project_path / "app" / "routes"
    if not routes_dir.exists():
        return 0

    patched = 0
    for rf in routes_dir.glob("*.py"):
        try:
            content = rf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if "__table__.columns.keys()" not in content:
            continue

        def _fix(m: re.Match) -> str:
            cls_name, schema_var, extra_args = m.group(1), m.group(2), m.group(3)
            explicit_kwargs = sorted(set(re.findall(r'(?:^|,)\s*(\w+)\s*=', extra_args)))
            if not explicit_kwargs:
                return m.group(0)
            exclude_set = "{" + ", ".join(f"'{k}'" for k in explicit_kwargs) + "}"
            filtered = (
                f"{{k: v for k, v in {schema_var}.dict().items() "
                f"if k in {cls_name}.__table__.columns.keys() and k not in {exclude_set}}}"
            )
            return f"{cls_name}(**{filtered}{extra_args})"

        new_content = _FILTERED_CTOR_KWARG_COLLISION_RE.sub(_fix, content)
        if new_content != content:
            rf.write_text(new_content, encoding="utf-8")
            patched += 1
            print(f"  [patcher] Fixed constructor kwarg collision in {rf.name}")

    return patched


_UNSAFE_HASATTR_COLUMN_FILTER_RE = re.compile(r"hasattr\(([A-Z]\w+),\s*k\)")


def _patch_unsafe_model_hasattr_filter(project_path: Path) -> int:
    """
    Rewrite `if hasattr(Model, k)` to `if k in Model.__table__.columns.keys()`
    in an already-generated project.

    _patch_star_dict_extra_fields (and the service-stub generator below) used
    to write this exact hasattr()-based filter as a defensive fix for
    TypeError on invalid constructor kwargs -- but hasattr() also returns True
    for a read-only @property with the same name as a schema field (e.g.
    `category` computed from a relationship), so the filter let it through
    and the constructor call raised AttributeError: property 'x' of 'Model'
    object has no setter, on every create request. Both generators were
    fixed to never produce this pattern again; this patches projects that
    already have it baked in from before that fix existed. Reproduced live
    on forge_expense_tracker's POST /expenses (500 on every request).
    """
    routes_dir = project_path / "app" / "routes"
    if not routes_dir.exists():
        return 0

    patched = 0
    for rf in routes_dir.glob("*.py"):
        try:
            content = rf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if "hasattr(" not in content:
            continue

        new_content = _UNSAFE_HASATTR_COLUMN_FILTER_RE.sub(
            lambda m: f"k in {m.group(1)}.__table__.columns.keys()",
            content,
        )
        if new_content != content:
            rf.write_text(new_content, encoding="utf-8")
            patched += 1
            print(f"  [patcher] Fixed unsafe hasattr() column filter in {rf.name}")

    return patched


_ATTR_ACCESS_RE = re.compile(r'\b(\w+)\.(\w+)\b')

_FIELD_SYNONYMS_PATCHER = {
    "username": ["email", "name", "full_name", "display_name"],
    "user_handle": ["username", "email", "name"],
    "display_name": ["full_name", "name", "username"],
    "full_name": ["display_name", "name", "username"],
    "status": ["state", "is_active", "is_done"],
    "state": ["status", "is_active"],
    "description": ["name", "title", "content", "body", "notes"],
    "content": ["body", "description", "text", "notes"],
    "title": ["name", "label", "heading"],
    "label": ["name", "title", "tag"],
    "priority": ["importance", "urgency", "level"],
    "due_date": ["deadline", "due_at", "expires_at"],
    "deadline": ["due_date", "due_at", "expires_at"],
    "creator_id": ["owner_id", "user_id", "created_by"],
    "author_id": ["owner_id", "user_id", "created_by"],
}


def _patch_attr_access_mismatches(project_path: Path) -> int:
    """Fix route files that access obj.invalid_attr where invalid_attr doesn't exist
    on the SQLAlchemy model (e.g. user.username when model only has email).

    Specifically fixes dict literals and return statements that hard-code
    attribute names not present on the model, using synonym mapping.
    """
    routes_dir = project_path / "app" / "routes"
    models_dir = project_path / "app" / "models"
    if not routes_dir.exists() or not models_dir.exists():
        return 0

    # Build {cls_name → frozenset(column_names)}
    model_cols: dict[str, frozenset] = {}
    for mf in models_dir.glob("*.py"):
        if mf.name == "__init__.py":
            continue
        try:
            src = mf.read_text(encoding="utf-8", errors="replace")
            for cls_m in re.finditer(r'^class\s+(\w+)\s*\(', src, re.MULTILINE):
                cls = cls_m.group(1)
                if cls in ("Base", "BaseModel", "Config", "Meta"):
                    continue
                cols = set(re.findall(r'^\s{4}(\w+)\s*=\s*Column\(', src, re.MULTILINE)) | {"id"}
                model_cols[cls] = frozenset(cols)
        except Exception:
            pass

    if not model_cols:
        return 0

    patched = 0
    for rf in routes_dir.glob("*.py"):
        try:
            content = rf.read_text(encoding="utf-8", errors="replace")
            original = content

            for cls_name, valid_cols in model_cols.items():
                # Only process route files that reference this class
                if cls_name not in content:
                    continue
                for bad_attr, candidates in _FIELD_SYNONYMS_PATCHER.items():
                    if bad_attr in valid_cols:
                        continue  # attribute exists, no fix needed
                    good_attr = next((c for c in candidates if c in valid_cols), None)
                    if not good_attr:
                        continue
                    # Replace .bad_attr with .good_attr only in attribute access context
                    # Use word boundary to avoid replacing "username_field" etc.
                    content = re.sub(
                        r'(?<!\w)\.' + re.escape(bad_attr) + r'\b',
                        '.' + good_attr,
                        content,
                    )

            if content != original:
                rf.write_text(content, encoding="utf-8")
                patched += 1
                print(f"  [patcher] Fixed attribute accesses in {rf.name}")
        except Exception:
            pass
    return patched


def _patch_missing_pydantic_imports(project_path: Path) -> int:
    """
    Scan all .py files in app/schemas/ and ensure they import the pydantic symbols
    they actually use (BaseModel, Field, Optional, List, Dict, Any, ConfigDict).
    Prevents 'Undefined symbol BaseModel' static validation failures.
    """
    schemas_dir = project_path / "app" / "schemas"
    if not schemas_dir.exists():
        return 0

    _PYDANTIC_SYMBOLS = {"BaseModel", "Field", "ConfigDict", "validator", "model_validator", "field_validator"}
    _TYPING_SYMBOLS = {"Optional", "List", "Dict", "Any", "Union", "Tuple", "Set"}
    # stdlib types the LLM annotates with but forgets to import. Each maps to its
    # canonical import. Missing these produced 'Undefined symbol datetime' —
    # which, when the fix loop's LLM added a datetime field, regressed and
    # reverted an otherwise-good habits fix on every attempt.
    _STDLIB_IMPORTS = {
        "datetime": "from datetime import datetime",
        "date":     "from datetime import date",
        "time":     "from datetime import time",
        "timedelta":"from datetime import timedelta",
        "Decimal":  "from decimal import Decimal",
        "UUID":     "from uuid import UUID",
        "Enum":     "from enum import Enum",
    }
    patched = 0

    def _symbol_available(content: str, sym: str) -> bool:
        # imported (from X import ... sym ... / import sym) or defined locally
        if re.search(rf'^\s*from\s+\S+\s+import\s+[^\n]*\b{sym}\b', content, re.MULTILINE):
            return True
        if re.search(rf'^\s*import\s+[^\n]*\b{sym}\b', content, re.MULTILINE):
            return True
        if re.search(rf'^\s*(?:class|def)\s+{sym}\b', content, re.MULTILINE):
            return True
        return False

    for py_file in schemas_dir.rglob("*.py"):
        try:
            content = py_file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        used_pydantic = {s for s in _PYDANTIC_SYMBOLS if re.search(r'\b' + s + r'\b', content)}
        used_typing = {s for s in _TYPING_SYMBOLS if re.search(r'\b' + s + r'\b', content)}

        if not used_pydantic and not used_typing:
            continue

        already_pydantic = set(re.findall(r'from pydantic import ([^\n]+)', content))
        already_pydantic_flat: set[str] = set()
        for line in already_pydantic:
            already_pydantic_flat |= {s.strip() for s in line.split(',')}

        already_typing = set(re.findall(r'from typing import ([^\n]+)', content))
        already_typing_flat: set[str] = set()
        for line in already_typing:
            already_typing_flat |= {s.strip() for s in line.split(',')}

        need_pydantic = used_pydantic - already_pydantic_flat
        need_typing = used_typing - already_typing_flat

        # stdlib type names used but neither imported nor locally defined.
        need_stdlib = [
            sym for sym in _STDLIB_IMPORTS
            if re.search(r'\b' + sym + r'\b', content) and not _symbol_available(content, sym)
        ]

        if not need_pydantic and not need_typing and not need_stdlib:
            continue

        lines = content.splitlines(keepends=True)
        insert_at = 0
        for i, line in enumerate(lines):
            if line.startswith(('import ', 'from ')):
                insert_at = i
                break

        inject = ""
        if need_pydantic:
            if already_pydantic_flat:
                all_p = sorted(already_pydantic_flat | need_pydantic)
                content = re.sub(
                    r'from pydantic import [^\n]+',
                    f'from pydantic import {", ".join(all_p)}',
                    content, count=1
                )
            else:
                inject += f'from pydantic import {", ".join(sorted(need_pydantic))}\n'
        if need_typing:
            if already_typing_flat:
                all_t = sorted(already_typing_flat | need_typing)
                content = re.sub(
                    r'from typing import [^\n]+',
                    f'from typing import {", ".join(all_t)}',
                    content, count=1
                )
            else:
                inject += f'from typing import {", ".join(sorted(need_typing))}\n'
        for sym in sorted(need_stdlib):
            inject += _STDLIB_IMPORTS[sym] + "\n"

        if inject:
            lines = content.splitlines(keepends=True)
            lines.insert(insert_at, inject)
            content = "".join(lines)

        try:
            py_file.write_text(content, encoding="utf-8")
            patched += 1
            _extra = f" stdlib={need_stdlib}" if need_stdlib else ""
            print(f"  [patcher] Fixed pydantic imports in {py_file.name}: pydantic={need_pydantic or '{}'} typing={need_typing or '{}'}{_extra}")
        except Exception:
            pass

    return patched


def _patch_orm_type_in_route_schemas(project_path: Path) -> int:
    """
    In route files, replace SQLAlchemy model types used inside Pydantic class
    field annotations with Any to prevent PydanticSchemaGenerationError.
    """
    routes_dir = project_path / "app" / "routes"
    models_dir = project_path / "app" / "models"
    if not routes_dir.exists() or not models_dir.exists():
        return 0

    # Collect all SQLAlchemy model class names
    orm_classes: set[str] = set()
    for mf in models_dir.glob("*.py"):
        if mf.name.startswith("_"):
            continue
        try:
            text = mf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for cls in re.findall(r"^class (\w+)\s*\(Base\)", text, re.MULTILINE):
            orm_classes.add(cls)

    if not orm_classes:
        return 0

    patched = 0
    for rf in routes_dir.glob("*.py"):
        if rf.name.startswith("_"):
            continue
        try:
            content = rf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        if not _PYDANTIC_CLASS_RE.search(content):
            continue  # no Pydantic models defined in this route file

        changed = False
        new_content = content
        for orm_cls in orm_classes:
            # List[OrmClass] → List[Any]
            list_pat = re.compile(rf'\bList\[{re.escape(orm_cls)}\]')
            if list_pat.search(new_content):
                new_content = list_pat.sub("List[Any]", new_content)
                changed = True
            # Optional[OrmClass] → Optional[Any]
            opt_pat = re.compile(rf'\bOptional\[{re.escape(orm_cls)}\]')
            if opt_pat.search(new_content):
                new_content = opt_pat.sub("Optional[Any]", new_content)
                changed = True
            # Field annotation: `name: OrmClass` (bare, not in List/Optional)
            bare_pat = re.compile(rf'(\b\w+\s*:\s*){re.escape(orm_cls)}(\s*(?:=|#|\n))')
            if bare_pat.search(new_content):
                new_content = bare_pat.sub(r'\1Any\2', new_content)
                changed = True

        if changed:
            # Ensure Any is imported
            if "from typing import" in new_content:
                if "Any" not in new_content:
                    new_content = re.sub(
                        r'(from typing import )([^\n]+)',
                        lambda m: m.group(0) if "Any" in m.group(2) else f"{m.group(1)}{m.group(2)}, Any",
                        new_content, count=1,
                    )
            else:
                new_content = "from typing import Any\n" + new_content
            rf.write_text(new_content, encoding="utf-8")
            patched += 1
            print(f"  [patcher] Fixed ORM types in route schema in {rf.name}")

    return patched


# ── Fix: paginated response_model=List[X] mismatch ───────────────────────────
# LLMs often generate a route that returns {"items": [...], "total": N}
# but decorates it with response_model=List[TaskResponse]. FastAPI then raises
# ResponseValidationError: Input should be a valid list.
# Fix: when a handler returns a dict with "items" key, strip List[] response_model.

def _patch_list_response_model_mismatch(project_path: Path) -> int:
    routes_dir = project_path / "app" / "routes"
    if not routes_dir.exists():
        return 0

    patched = 0
    for rf in routes_dir.glob("*.py"):
        if rf.name.startswith("_"):
            continue
        try:
            content = rf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        if "response_model=List[" not in content or '"items"' not in content:
            continue

        lines = content.split("\n")
        out = list(lines)
        changed = False
        i = 0
        while i < len(lines):
            line = lines[i]
            if re.search(r"@\w+_router\.\w+\(", line) and "response_model=List[" in line:
                # Find the def line (may be next line or after multi-line decorator)
                j = i + 1
                while j < len(lines) and not lines[j].lstrip().startswith("def "):
                    j += 1
                # Scan function body for return {"items":
                k = j + 1
                found = False
                while k < len(lines):
                    kl = lines[k].strip()
                    if re.match(r"@\w+_router", kl) or kl.startswith("def "):
                        break
                    if re.search(r'return\s*\{[^}]*["\']items["\']', kl):
                        found = True
                        break
                    k += 1
                if found:
                    new_line = re.sub(r",?\s*response_model\s*=\s*List\[[\w\[\], ]+\]", "", out[i])
                    if new_line != out[i]:
                        out[i] = new_line
                        changed = True
            i += 1

        if changed:
            rf.write_text("\n".join(out), encoding="utf-8")
            patched += 1
            print(f"  [patcher] Removed mismatched List response_model in {rf.name}")

    return patched


# ── Fix: missing npm packages in frontend package.json ───────────────────────
# LLMs import @mui/material, react-router-dom etc. in JSX but forget to add
# them to package.json. The Cloudflare/Vite build then fails with
# "Rollup failed to resolve import @mui/material".

_FRONTEND_PKG_PEERS: dict[str, list[str]] = {
    "@mui/material": ["@mui/material", "@emotion/react", "@emotion/styled"],
    "@mui/icons-material": ["@mui/icons-material", "@mui/material", "@emotion/react", "@emotion/styled"],
    "@mui/x-date-pickers": ["@mui/x-date-pickers", "dayjs"],
    "@tanstack/react-query": ["@tanstack/react-query"],
    "react-query": ["react-query"],
    "react-router-dom": ["react-router-dom"],
    "axios": ["axios"],
    "react-hook-form": ["react-hook-form"],
    "recharts": ["recharts"],
    "react-chartjs-2": ["react-chartjs-2", "chart.js"],
    "chart.js": ["chart.js"],
    "date-fns": ["date-fns"],
    "dayjs": ["dayjs"],
    "zod": ["zod"],
    "react-toastify": ["react-toastify"],
    "react-hot-toast": ["react-hot-toast"],
    "framer-motion": ["framer-motion"],
    "lucide-react": ["lucide-react"],
    "clsx": ["clsx"],
}

_JSX_IMPORT_RE = re.compile(
    r"""(?:from|import)\s+['"](@?[\w][\w.-]*/[\w.-]+|@?[\w][\w.-]*)['"]"""
)

# Packages where "latest" resolves to a version incompatible with what LLMs
# actually generate (trained on an older, more common API). Chakra UI v3
# (the "latest" tag since 2024) removed useToast entirely in favor of a
# completely different toaster/createToaster API and requires a different
# <ChakraProvider value={system}> setup -- LLM-generated Chakra code is
# almost always v2-shaped. Pinning avoids a build break that's invisible
# until the component is actually reachable (see
# _patch_wire_orphan_frontend_routes: SettingsPage.jsx's `useToast` import
# only started failing the build once that patch made the page reachable).
_FRONTEND_PKG_VERSION_OVERRIDES: dict[str, str] = {
    "@chakra-ui/react": "^2.8.2",
}


def _patch_frontend_package_json(project_path: Path) -> bool:
    """
    Scan src/*.jsx for npm package imports and add any missing packages to
    package.json so Vite can resolve them at build time.
    """
    import json as _json

    pkg_json = project_path / "package.json"
    src_dir = project_path / "src"
    if not pkg_json.exists() or not src_dir.exists():
        return False

    try:
        pkg = _json.loads(pkg_json.read_text(encoding="utf-8"))
    except Exception:
        return False

    # Re-pin any already-present dependency that has drifted onto "latest"
    # (e.g. from an earlier patcher run, before an override existed).
    repinned = False
    for name, version in _FRONTEND_PKG_VERSION_OVERRIDES.items():
        deps = pkg.get("dependencies", {})
        if name in deps and deps[name] != version:
            deps[name] = version
            repinned = True

    all_installed: set[str] = set(pkg.get("dependencies", {}).keys()) | set(pkg.get("devDependencies", {}).keys())

    imported: set[str] = set()
    for jsfile in list(src_dir.rglob("*.jsx")) + list(src_dir.rglob("*.js")) + list(src_dir.rglob("*.tsx")):
        try:
            text = jsfile.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for m in _JSX_IMPORT_RE.finditer(text):
            name = m.group(1)
            if name.startswith(".") or name.startswith("/"):
                continue
            # Always resolve subpath imports to the root package:
            # @mui/icons-material/Menu → @mui/icons-material
            # react-dom/client → react-dom
            # @tanstack/react-query/devtools → @tanstack/react-query
            pkg_root = name.split("/")[0] if not name.startswith("@") else "/".join(name.split("/")[:2])
            # Skip if the root package is already installed
            if pkg_root in all_installed:
                continue
            # Always add the root package (not the subpath) so peer lookup works
            imported.add(pkg_root)

    to_add: set[str] = set()
    for name in imported:
        if name not in all_installed:
            # Expand to peer deps (e.g. @mui/material → also @emotion/react)
            extras = _FRONTEND_PKG_PEERS.get(name, [name])
            to_add.update(e for e in extras if e not in all_installed)

    if not to_add:
        if repinned:
            pkg_json.write_text(_json.dumps(pkg, indent=2), encoding="utf-8")
            print("  [patcher] Re-pinned frontend package version override(s)")
        return repinned

    if "dependencies" not in pkg:
        pkg["dependencies"] = {}
    for name in sorted(to_add):
        pkg["dependencies"][name] = _FRONTEND_PKG_VERSION_OVERRIDES.get(name, "latest")

    pkg_json.write_text(_json.dumps(pkg, indent=2), encoding="utf-8")
    print(f"  [patcher] Added missing frontend packages to package.json: {sorted(to_add)}")
    return True


# ── Fix: missing app/services/ stubs ─────────────────────────────────────────
# LLMs sometimes generate route files that delegate to a service layer
# (from app.services.team_service import create_team, ...) but don't generate
# those service files. Validation then fails with "Missing import target".
# Create working CRUD stubs so static validation passes and routes function.

_SVC_IMPORT_RE = re.compile(r"^from app\.services\.(\w+) import ([^\n]+)", re.MULTILINE)


def _infer_crud_func(func: str, model_cls: str, resource: str) -> str:
    f = func.lower()
    rid = f"{resource}_id"
    if re.match(r"get_\w+_by_id|get_by_id|fetch_\w+_by_id", f):
        return (f"def {func}(db: Session, {rid}: int):\n"
                f"    return db.query({model_cls}).filter({model_cls}.id == {rid}).first()\n")
    if re.match(r"get_all_\w+|list_\w+|get_\w+s$", f):
        return (f"def {func}(db: Session, limit: int = 100, offset: int = 0, **kw):\n"
                f"    return db.query({model_cls}).offset(offset).limit(limit).all()\n")
    if re.match(r"create_\w+", f):
        # Filter against __table__.columns, not hasattr(): a model exposing a
        # read-only @property with the same name as a schema field passes
        # hasattr() too, and the constructor call then raises AttributeError
        # ("property 'x' has no setter") on every create request.
        return (f"def {func}(db: Session, {resource}_in=None, **kw):\n"
                f"    data = {resource}_in.dict() if hasattr({resource}_in, 'dict') else kw\n"
                f"    obj = {model_cls}(**{{k: v for k, v in data.items() if k in {model_cls}.__table__.columns.keys()}})\n"
                f"    db.add(obj); db.commit(); db.refresh(obj); return obj\n")
    if re.match(r"update_\w+", f):
        return (f"def {func}(db: Session, {rid}: int, {resource}_in=None, **kw):\n"
                f"    obj = db.query({model_cls}).filter({model_cls}.id == {rid}).first()\n"
                f"    if not obj: return None\n"
                f"    data = {resource}_in.dict() if hasattr({resource}_in, 'dict') else kw\n"
                f"    [setattr(obj, k, v) for k, v in data.items() if k in {model_cls}.__table__.columns.keys()]\n"
                f"    db.commit(); db.refresh(obj); return obj\n")
    if re.match(r"delete_\w+", f):
        return (f"def {func}(db: Session, {rid}: int) -> bool:\n"
                f"    obj = db.query({model_cls}).filter({model_cls}.id == {rid}).first()\n"
                f"    if not obj: return False\n"
                f"    db.delete(obj); db.commit(); return True\n")
    if re.match(r"add_\w+_to_\w+|remove_\w+_from_\w+", f):
        return (f"def {func}(db: Session, {rid}: int, user_id: int, **kw):\n"
                f"    return db.query({model_cls}).filter({model_cls}.id == {rid}).first()\n")
    # Generic fallback
    return (f"def {func}(db: Session, *args, **kw):\n    return None\n")


def _patch_create_missing_service_stubs(project_path: Path) -> int:
    routes_dir = project_path / "app" / "routes"
    services_dir = project_path / "app" / "services"
    models_dir = project_path / "app" / "models"
    if not routes_dir.exists():
        return 0

    # Collect all service imports from route files
    needed: dict[str, set[str]] = {}
    for rf in routes_dir.glob("*.py"):
        try:
            content = rf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for m in _SVC_IMPORT_RE.finditer(content):
            module = m.group(1)
            funcs = {n.strip().split(" as ")[-1].strip() for n in m.group(2).split(",") if n.strip()}
            needed.setdefault(module, set()).update(funcs)

    if not needed:
        return 0

    # Build model class map: service name → (ModelClass, module path)
    model_map: dict[str, tuple[str, str]] = {}
    if models_dir.exists():
        for mf in models_dir.glob("*.py"):
            if mf.name.startswith("_"):
                continue
            try:
                text = mf.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for cls in re.findall(r"^class (\w+)\s*\(Base\)", text, re.MULTILINE):
                for key in (mf.stem, mf.stem.rstrip("s"), cls.lower(), cls.lower().rstrip("s")):
                    if key not in model_map:
                        model_map[key] = (cls, f"app.models.{mf.stem}")

    services_dir.mkdir(parents=True, exist_ok=True)
    created = 0

    for module, funcs in needed.items():
        svc_file = services_dir / f"{module}.py"

        # Determine which funcs are missing
        if svc_file.exists():
            existing = svc_file.read_text(encoding="utf-8", errors="replace")
            missing = {f for f in funcs if not re.search(rf"^def {re.escape(f)}\b", existing, re.MULTILINE)}
            if not missing:
                continue
        else:
            existing = None
            missing = funcs

        # Infer resource name and model class
        resource = module.replace("_service", "").replace("_services", "")
        model_cls, model_mod = model_map.get(resource) or model_map.get(resource.rstrip("s")) or (resource.capitalize(), f"app.models.{resource}")

        stubs = [_infer_crud_func(f, model_cls, resource) for f in sorted(missing)]

        if existing is None:
            content = (
                f"from sqlalchemy.orm import Session\n"
                f"from typing import List, Optional\n"
                f"try:\n"
                f"    from {model_mod} import {model_cls}\n"
                f"except ImportError:\n"
                f"    {model_cls} = object\n\n"
                + "\n".join(stubs)
            )
        else:
            content = existing.rstrip() + "\n\n" + "\n".join(stubs) + "\n"

        svc_file.write_text(content, encoding="utf-8")
        created += 1
        print(f"  [patcher] Created service stub {module}.py ({len(missing)} function(s): {sorted(missing)})")

    return created


# ── Inject missing db.refresh() after db.commit() ────────────────────────────
# LLMs frequently forget db.refresh(obj) after db.commit() in POST handlers.
# Without it the in-memory SQLAlchemy object still has id=None even though the
# row was persisted with a real autoincrement id, so the response body returns
# {"id": null, ...} and every downstream CRUD step (Edit/Delete/Verify) fails.

_ADD_COMMIT_RE = re.compile(
    r"([ \t]*)db\.add\((\w+)\)\n(\1db\.commit\(\)\n)",
    re.MULTILINE,
)


def _patch_missing_db_refresh(project_path: Path) -> int:
    """
    For every POST/create route handler that calls db.add(obj) + db.commit()
    without an immediately following db.refresh(obj), inject the refresh call.
    """
    routes_dir = project_path / "app" / "routes"
    if not routes_dir.exists():
        return 0

    patched = 0
    for rf in sorted(routes_dir.glob("*.py")):
        if rf.name == "__init__.py":
            continue
        try:
            src = rf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        if "db.add(" not in src or "db.commit()" not in src:
            continue

        original = src
        result: list[str] = []
        pos = 0

        for m in _ADD_COMMIT_RE.finditer(src):
            result.append(src[pos:m.end()])
            indent = m.group(1)
            varname = m.group(2)
            # Check whether refresh is already present in the next 3 lines
            after = src[m.end():]
            next_lines = after.split("\n")[:3]
            if not any(f"db.refresh({varname})" in ln for ln in next_lines):
                result.append(f"{indent}db.refresh({varname})\n")
            pos = m.end()

        result.append(src[pos:])
        new_src = "".join(result)

        if new_src != original:
            rf.write_text(new_src, encoding="utf-8")
            patched += 1
            print(f"  [refresh_patcher] Injected db.refresh() in {rf.name}")

    return patched


# ── Auto-wire orphan routers into main.py ─────────────────────────────────────

def _patch_wire_orphan_routers(project_path: Path) -> None:
    """
    Scan app/routes/*.py for every *_router = APIRouter(...) definition.
    For any router not already imported and included in main.py, inject:
      from app.routes.<module> import <router_name>
      app.include_router(<router_name>)
    Eliminates 'Orphan file: X is not imported by main.py' errors.
    """
    routes_dir = project_path / "app" / "routes"
    main_py = project_path / "app" / "main.py"
    if not routes_dir.exists() or not main_py.exists():
        return

    try:
        main_content = main_py.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return

    router_def_re = re.compile(r"^(\w+_router)\s*=\s*APIRouter\b", re.MULTILINE)
    original = main_content
    added: list[str] = []

    for route_file in sorted(routes_dir.glob("*.py")):
        if route_file.name == "__init__.py":
            continue
        try:
            content = route_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        module = route_file.stem  # e.g. "team_routes"

        for m in router_def_re.finditer(content):
            router_name = m.group(1)  # e.g. "team_router"
            import_line = f"from app.routes.{module} import {router_name}"
            include_line = f"app.include_router({router_name})"

            import_present = import_line in main_content
            include_present = f"app.include_router({router_name}" in main_content

            if import_present and include_present:
                continue

            if not import_present:
                # Insert after the last "from app.routes.* import *_router" line
                last_routes_end = 0
                for im in re.finditer(
                    r"^from app\.routes\.\w+ import \w+_router\n", main_content, re.MULTILINE
                ):
                    last_routes_end = im.end()

                if last_routes_end:
                    main_content = (
                        main_content[:last_routes_end]
                        + import_line + "\n"
                        + main_content[last_routes_end:]
                    )
                else:
                    # Insert after first "from app." import
                    m2 = re.search(r"^from app\.", main_content, re.MULTILINE)
                    if m2:
                        eol = main_content.index("\n", m2.start()) + 1
                        main_content = main_content[:eol] + import_line + "\n" + main_content[eol:]
                    else:
                        main_content = import_line + "\n" + main_content

            if not include_present:
                # Insert after the last "app.include_router(...)" call
                last_include_end = 0
                for im in re.finditer(
                    r"^app\.include_router\([^)]+\)\n", main_content, re.MULTILINE
                ):
                    last_include_end = im.end()

                if last_include_end:
                    main_content = (
                        main_content[:last_include_end]
                        + include_line + "\n"
                        + main_content[last_include_end:]
                    )
                else:
                    # Before first @app. decorator, or at end
                    m3 = re.search(r"^@app\.", main_content, re.MULTILINE)
                    if m3:
                        main_content = (
                            main_content[:m3.start()]
                            + include_line + "\n\n"
                            + main_content[m3.start():]
                        )
                    else:
                        main_content = main_content.rstrip() + f"\n{include_line}\n"

            added.append(router_name)

    if main_content != original:
        main_py.write_text(main_content, encoding="utf-8")
        print(f"  [router_patcher] Wired {len(added)} orphan router(s) into main.py: {', '.join(added)}")


_LOGIN_PAGE_TEMPLATE = '''\
import React from 'react';
import { useNavigate, Link } from 'react-router-dom';
import API from '../api';

const parseError = (err) => {
  if (!err.response) return null;
  const detail = err.response?.data?.detail;
  if (!detail) return 'Something went wrong. Please try again.';
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map(d => d.msg).join(', ');
  return 'Something went wrong. Please try again.';
};

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

const LoginPage = () => {
  const [email, setEmail] = React.useState('');
  const [password, setPassword] = React.useState('');
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState(null);
  const [status, setStatus] = React.useState(null);
  const navigate = useNavigate();

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setStatus(null);

    for (let attempt = 1; attempt <= 3; attempt++) {
      try {
        setStatus(attempt === 1 ? 'Signing in...' : `Backend starting up... retrying (${attempt}/3)`);
        const res = await API.post('/auth/login', { email, password });
        localStorage.setItem('token', res.data.access_token);
        if (res.data.display_name) localStorage.setItem('display_name', res.data.display_name);
        navigate('/dashboard');
        return;
      } catch (err) {
        const msg = parseError(err);
        if (msg) { setError(msg); setStatus(null); setLoading(false); return; }
        if (attempt < 3) { setStatus(`Backend took too long. Retrying in 15s (${attempt}/3)`); await sleep(15000); }
      }
    }
    setError('Backend took too long to respond. Please wait 30 seconds and try again.');
    setLoading(false);
  };

  return (
    <div className="min-h-screen bg-slate-50 dark:bg-slate-900 flex items-center justify-center p-4 relative overflow-hidden">
      <div className="fixed inset-0 overflow-hidden pointer-events-none -z-10">
        <div className="absolute -top-24 -right-24 w-96 h-96 rounded-full bg-gradient-to-br from-indigo-500 to-violet-500 opacity-20 dark:opacity-10 blur-3xl" />
        <div className="absolute bottom-0 left-1/4 w-72 h-72 rounded-full bg-gradient-to-br from-indigo-500 to-violet-500 opacity-10 dark:opacity-[0.07] blur-3xl" />
      </div>
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-indigo-500 to-violet-500 mx-auto mb-3 flex items-center justify-center shadow-lg shadow-indigo-500/30">
            <span className="text-white font-bold text-xl">A</span>
          </div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-white">Welcome back</h1>
          <p className="text-slate-500 dark:text-slate-400 text-sm mt-1">Sign in to your account</p>
        </div>
        <div className="bg-white/80 dark:bg-slate-800/70 backdrop-blur-xl rounded-2xl shadow-sm ring-1 ring-black/5 dark:ring-white/5 border border-slate-100 dark:border-slate-700/60 p-6 space-y-4">
          {error && <p className="text-sm text-red-600 dark:text-red-400 text-center">{error}</p>}
          {status && <p className="text-sm text-slate-500 dark:text-slate-400 text-center">{status}</p>}
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-1">
              <label className="text-xs font-medium text-slate-700 dark:text-slate-300">Email</label>
              <input type="email" value={email} onChange={e => setEmail(e.target.value)} placeholder="you@example.com" className="input" required disabled={loading} />
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium text-slate-700 dark:text-slate-300">Password</label>
              <input type="password" value={password} onChange={e => setPassword(e.target.value)} placeholder="********" className="input" required disabled={loading} />
            </div>
            <button
              type="submit"
              disabled={loading || !email || !password}
              className="w-full justify-center inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-white font-medium bg-gradient-to-r from-indigo-500 to-violet-500 hover:opacity-90 active:scale-[0.97] shadow-lg shadow-indigo-500/25 transition-all duration-150 disabled:opacity-50 disabled:pointer-events-none"
            >
              {loading ? 'Signing in...' : 'Sign In'}
            </button>
          </form>
        </div>
        <p className="text-center text-sm text-slate-500 mt-4">
          Don't have an account? <Link to="/register" className="font-medium hover:underline bg-gradient-to-r from-indigo-500 to-violet-500 bg-clip-text text-transparent">Sign up</Link>
        </p>
      </div>
    </div>
  );
};

export default LoginPage;
'''

_REGISTER_PAGE_TEMPLATE = '''\
import React from 'react';
import { useNavigate, Link } from 'react-router-dom';
import API from '../api';

const parseError = (err) => {
  if (!err.response) return null;
  const detail = err.response?.data?.detail;
  if (!detail) return 'Something went wrong. Please try again.';
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map(d => d.msg).join(', ');
  return 'Something went wrong. Please try again.';
};

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

const RegisterPage = () => {
  const [email, setEmail] = React.useState('');
  const [password, setPassword] = React.useState('');
  const [displayName, setDisplayName] = React.useState('');
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState(null);
  const [status, setStatus] = React.useState(null);
  const navigate = useNavigate();

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setStatus(null);

    if (password.length < 8) {
      setError('Password must be at least 8 characters.');
      setLoading(false);
      return;
    }

    for (let attempt = 1; attempt <= 3; attempt++) {
      try {
        setStatus(attempt === 1 ? 'Creating account...' : `Backend starting up... retrying (${attempt}/3)`);
        const res = await API.post('/auth/register', { email, password, display_name: displayName });
        localStorage.setItem('token', res.data.access_token);
        if (res.data.display_name) localStorage.setItem('display_name', res.data.display_name);
        navigate('/dashboard');
        return;
      } catch (err) {
        const msg = parseError(err);
        if (msg) { setError(msg); setStatus(null); setLoading(false); return; }
        if (attempt < 3) { setStatus(`Backend took too long. Retrying in 15s (${attempt}/3)`); await sleep(15000); }
      }
    }
    setError('Backend took too long to respond. Please wait 30 seconds and try again.');
    setLoading(false);
  };

  return (
    <div className="min-h-screen bg-slate-50 dark:bg-slate-900 flex items-center justify-center p-4 relative overflow-hidden">
      <div className="fixed inset-0 overflow-hidden pointer-events-none -z-10">
        <div className="absolute -top-24 -right-24 w-96 h-96 rounded-full bg-gradient-to-br from-indigo-500 to-violet-500 opacity-20 dark:opacity-10 blur-3xl" />
        <div className="absolute bottom-0 left-1/4 w-72 h-72 rounded-full bg-gradient-to-br from-indigo-500 to-violet-500 opacity-10 dark:opacity-[0.07] blur-3xl" />
      </div>
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-indigo-500 to-violet-500 mx-auto mb-3 flex items-center justify-center shadow-lg shadow-indigo-500/30">
            <span className="text-white font-bold text-xl">A</span>
          </div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-white">Create an account</h1>
          <p className="text-slate-500 dark:text-slate-400 text-sm mt-1">Get started in seconds</p>
        </div>
        <div className="bg-white/80 dark:bg-slate-800/70 backdrop-blur-xl rounded-2xl shadow-sm ring-1 ring-black/5 dark:ring-white/5 border border-slate-100 dark:border-slate-700/60 p-6 space-y-4">
          {error && <p className="text-sm text-red-600 dark:text-red-400 text-center">{error}</p>}
          {status && <p className="text-sm text-slate-500 dark:text-slate-400 text-center">{status}</p>}
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-1">
              <label className="text-xs font-medium text-slate-700 dark:text-slate-300">Display name</label>
              <input type="text" value={displayName} onChange={e => setDisplayName(e.target.value)} placeholder="Jane Doe" className="input" required disabled={loading} />
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium text-slate-700 dark:text-slate-300">Email</label>
              <input type="email" value={email} onChange={e => setEmail(e.target.value)} placeholder="you@example.com" className="input" required disabled={loading} />
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium text-slate-700 dark:text-slate-300">Password</label>
              <input type="password" value={password} onChange={e => setPassword(e.target.value)} placeholder="At least 8 characters" className="input" required disabled={loading} />
            </div>
            <button
              type="submit"
              disabled={loading || !email || !password || !displayName}
              className="w-full justify-center inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-white font-medium bg-gradient-to-r from-indigo-500 to-violet-500 hover:opacity-90 active:scale-[0.97] shadow-lg shadow-indigo-500/25 transition-all duration-150 disabled:opacity-50 disabled:pointer-events-none"
            >
              {loading ? 'Creating account...' : 'Sign Up'}
            </button>
          </form>
        </div>
        <p className="text-center text-sm text-slate-500 mt-4">
          Already have an account? <Link to="/login" className="font-medium hover:underline bg-gradient-to-r from-indigo-500 to-violet-500 bg-clip-text text-transparent">Sign in</Link>
        </p>
      </div>
    </div>
  );
};

export default RegisterPage;
'''


def patch_ensure_auth_pages(project_path: Path) -> int:
    """
    App.jsx's PrivateRoute pattern always redirects an unauthenticated user
    to `<Navigate to="/login">` -- but this is a bare string the LLM has to
    remember to also back with an actual LoginPage.jsx and `<Route
    path="/login">`. Seen live: the LLM generated zero auth pages at all
    (no LoginPage.jsx, no RegisterPage.jsx, no /login route anywhere), so
    every unauthenticated visit hit PrivateRoute -> Navigate to "/login" ->
    matched nothing -> fell through to the mandatory wildcard "*" -> Navigate
    to "/dashboard" -> PrivateRoute -> "/login" again, forever. This is a
    genuine infinite client-side redirect loop: Chrome's own IPC-flood
    protection kicked in ("Throttling navigation to prevent the browser from
    hanging"), the page never rendered anything, and the app was permanently
    blank for every user with no way to ever log in -- not just an
    inconvenience, a total-outage bug invisible to every other automated
    check (compile, CRUD journey, endpoint smoke tests all use a token
    obtained directly from the API, never by clicking through the UI).

    Synthesizes known-good LoginPage.jsx / RegisterPage.jsx (styled to match
    the shared index.css/api.js conventions) and wires them directly with
    NO PrivateRoute wrapper -- deliberately not reusing
    _patch_wire_orphan_frontend_routes below, which clones an existing
    authenticated route's wrapper (PrivateRoute) for anything it wires;
    doing that here would wrap the login page itself in the very auth guard
    it exists to satisfy, recreating this exact bug.
    """
    app_jsx = project_path / "src" / "App.jsx"
    pages_dir = project_path / "src" / "pages"
    if not app_jsx.exists() or not pages_dir.exists():
        return 0
    try:
        content = app_jsx.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return 0

    # Only apps using the standard token-gated auth pattern need these pages
    # at all -- if the app never references /login, it's out of scope here.
    if "/login" not in content:
        return 0

    login_path = pages_dir / "LoginPage.jsx"
    register_path = pages_dir / "RegisterPage.jsx"

    added: list[tuple] = []
    synthesized_login = not login_path.exists()
    if synthesized_login:
        login_path.write_text(_LOGIN_PAGE_TEMPLATE, encoding="utf-8")
        added.append(("LoginPage", "/login"))
    # _LOGIN_PAGE_TEMPLATE's own "Sign up" link points to /register -- if
    # we're synthesizing Login from scratch, Register must exist too or the
    # link is a dead end, regardless of whether the ORIGINAL App.jsx (before
    # this patch) ever mentioned /register at all.
    if (synthesized_login or "/register" in content) and not register_path.exists():
        register_path.write_text(_REGISTER_PAGE_TEMPLATE, encoding="utf-8")
        added.append(("RegisterPage", "/register"))

    if not added:
        return 0

    original = content
    last_import_m = list(re.finditer(r"^import\s+.+$", content, re.MULTILINE))
    insert_at = last_import_m[-1].end() if last_import_m else 0
    import_lines = "".join(f"\nimport {name} from './pages/{name}'" for name, _p in added)
    content = content[:insert_at] + import_lines + content[insert_at:]

    route_lines = "".join(
        f'\n        <Route path="{p}" element={{<{name} />}} />'
        for name, p in added
        if f'path="{p}"' not in content
    )
    wildcard_m = re.search(r'\n(\s*)<Route\s+path="\*"', content)
    if wildcard_m:
        content = content[:wildcard_m.start()] + route_lines + content[wildcard_m.start():]
    else:
        content = content.rstrip() + route_lines + "\n"

    if content != original:
        app_jsx.write_text(content, encoding="utf-8")
        print(f"  [patcher] Synthesized missing auth page(s) (no PrivateRoute wrapper) "
              f"and wired route(s): {', '.join(n for n, _p in added)}")

    return len(added)


def _patch_wire_orphan_frontend_routes(project_path: Path) -> None:
    """
    Frontend mirror of _patch_wire_orphan_routers above: App.jsx routinely
    imports every page component it generates, but the LLM sometimes forgets
    to give one a <Route> entry. The page exists, the sidebar links to it,
    but navigating there matches nothing and falls through to the mandatory
    wildcard "*" -> /dashboard redirect -- every automated check (compile,
    CRUD journey, endpoint smoke tests) passes because none of them click
    through the app's own navigation. Only visible by actually using it.

    Also handles a worse variant of the same problem: frontend_scaffold_
    service.ensure_app_jsx synthesizes App.jsx exactly once, from whatever
    pages exist on disk *at that moment* -- but the missing-frontend-import
    fix loop routinely creates additional page files (e.g. BadgesPage.jsx,
    because Navigation.jsx already links to /badges) afterward. Those pages
    are never imported into App.jsx at all, let alone routed, since the
    scaffold never re-runs. Reported live: habit-forge's Dashboard/Habits
    routes worked but Reports/Badges/Settings all silently bounced back to
    /dashboard -- "every other page is broken" from the user's perspective,
    while every automated check stayed green because none of them click
    through the sidebar. Any page file under src/pages/ that isn't imported
    anywhere in App.jsx gets an import added here first, so the existing
    orphan-route logic below picks it up the same as an already-imported one.
    """
    app_jsx = project_path / "src" / "App.jsx"
    if not app_jsx.exists():
        return
    try:
        content = app_jsx.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return
    original = content

    # Page components imported into App.jsx: import X from './pages/Y'
    import_re = re.compile(r"^import\s+(\w+)\s+from\s+['\"]\./pages/(\w+)['\"]", re.MULTILINE)
    imported_pairs = import_re.findall(content)
    imported_modules = {module for _name, module in imported_pairs}

    pages_dir = project_path / "src" / "pages"
    if pages_dir.is_dir():
        new_imports = []
        for pf in sorted(pages_dir.glob("*.jsx")):
            if pf.stem in imported_modules:
                continue
            try:
                page_src = pf.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            m = re.search(r"export\s+default\s+(?:function\s+)?(\w+)", page_src)
            if not m or m.group(1) in {n for n, _mod in imported_pairs}:
                continue
            new_imports.append((m.group(1), pf.stem))

        if new_imports:
            last_import_m = list(re.finditer(r"^import\s+.+$", content, re.MULTILINE))
            insert_at = last_import_m[-1].end() if last_import_m else 0
            addition = "".join(f"\nimport {name} from './pages/{module}'" for name, module in new_imports)
            content = content[:insert_at] + addition + content[insert_at:]
            imported_pairs = imported_pairs + new_imports
            print(f"  [route_patcher] Imported {len(new_imports)} orphan page file(s) into App.jsx: "
                  f"{', '.join(n for n, _m in new_imports)}")

    imported = [name for name, _module in imported_pairs]
    if not imported:
        if content != original:
            app_jsx.write_text(content, encoding="utf-8")
        return

    # A component is "routed" if it appears as a JSX tag anywhere (self-closing
    # or with props) -- App.jsx never renders a page component except inside a
    # <Route element={...}>.
    orphans = [name for name in imported if not re.search(rf"<{name}[\s/>]", content)]
    if not orphans:
        if content != original:
            app_jsx.write_text(content, encoding="utf-8")
        return

    routed_paths = set(re.findall(r'<Route\s+path="([^"]+)"', content))

    # Candidate URL paths: every to="/..." link anywhere under src/ (sidebar,
    # nav, etc.) that isn't already routed -- these are the LLM's own stated
    # intent for where each page should live, more reliable than guessing
    # from the component name alone.
    nav_paths: set[str] = set()
    src_dir = project_path / "src"
    for jf in src_dir.rglob("*.jsx"):
        try:
            t = jf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        nav_paths |= set(re.findall(r'to="(/[a-zA-Z0-9/_-]*)"', t))
    candidate_paths = sorted(nav_paths - routed_paths)

    # Clone an existing authenticated route's wrapper (PrivateRoute/Layout/...)
    # so the new route matches this project's own auth-guarding convention
    # instead of guessing at one.
    template_m = None
    for anchor_path in ("/dashboard", "/habits"):
        template_m = re.search(
            rf'(\s*)<Route\s+path="{re.escape(anchor_path)}"\s+element=\{{(.*?)\}}\s*/>',
            content, re.DOTALL,
        )
        if template_m:
            break
    if not template_m:
        if content != original:
            app_jsx.write_text(content, encoding="utf-8")
        return
    indent, template_element = template_m.groups()

    def _kebab_path(name: str) -> str:
        s = re.sub(r"(?<!^)(?=[A-Z])", "-", name).lower().replace("-page", "")
        return "/" + s

    added: list[tuple] = []
    for comp_name in orphans:
        comp_key = comp_name.lower().replace("page", "")
        target_path = None
        for p in candidate_paths:
            last_seg = p.rstrip("/").split("/")[-1].replace("-", "")
            if last_seg and (last_seg in comp_key or comp_key in last_seg):
                target_path = p
                break
        if target_path is None:
            target_path = _kebab_path(comp_name)
        if target_path in routed_paths:
            continue

        new_element = re.sub(r"<\w+\s*/>", f"<{comp_name} />", template_element, count=1)
        new_route = f'{indent}<Route path="{target_path}" element={{{new_element}}} />'

        wildcard_m = re.search(r'\n(\s*)<Route\s+path="\*"', content)
        if wildcard_m:
            content = content[:wildcard_m.start()] + "\n" + new_route + content[wildcard_m.start():]
        else:
            content = content.rstrip() + "\n" + new_route + "\n"

        routed_paths.add(target_path)
        candidate_paths = [p for p in candidate_paths if p != target_path]
        added.append((comp_name, target_path))

    if content != original:
        app_jsx.write_text(content, encoding="utf-8")
        summary = ", ".join(f"{c} -> {p}" for c, p in added)
        print(f"  [route_patcher] Wired {len(added)} orphan page(s) into App.jsx: {summary}")


# ── Main entry point ──────────────────────────────────────────────────────────

def _patch_schema_nullable_required_mismatch(project_path: Path) -> int:
    """
    Deterministic fix for the recurring 'SchemaX.field required but model
    allows NULL' validator error. Mirrors schema_model_validator exactly:
    for each schema class prefix-matched to a model, any required
    (non-Optional-annotated) field whose model column is nullable=True gets
    rewritten to `Optional[T] = None`, per the project contract.

    The LLM fix loop handled this badly in practice — it kept "fixing" the
    model side by writing a NEW model file (user.py next to users.py),
    triggering duplicate-model cleanup and FK breakage. One line of schema
    change is the correct, safe direction.
    """
    import ast as _ast
    from app.services.schema_model_validator import _is_optional_annotation

    models_dir = project_path / "app" / "models"
    schemas_dir = project_path / "app" / "schemas"
    if not models_dir.exists() or not schemas_dir.exists():
        return 0

    # ModelName -> {column: nullable}
    models: dict[str, dict] = {}
    for mf in models_dir.glob("*.py"):
        try:
            tree = _ast.parse(mf.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        for node in _ast.walk(tree):
            if not isinstance(node, _ast.ClassDef):
                continue
            fields = {}
            for child in node.body:
                if (isinstance(child, _ast.Assign) and isinstance(child.value, _ast.Call)
                        and getattr(child.value.func, "id", "") == "Column"):
                    nullable = False
                    for kw in child.value.keywords:
                        if kw.arg == "nullable" and isinstance(kw.value, _ast.Constant):
                            nullable = kw.value.value
                    for t in child.targets:
                        if isinstance(t, _ast.Name):
                            fields[t.id] = nullable
            if fields:
                models[node.name] = fields

    if not models:
        return 0

    patched = 0
    for sf in schemas_dir.glob("*.py"):
        if sf.name == "__init__.py":
            continue
        try:
            src = sf.read_text(encoding="utf-8", errors="replace")
            tree = _ast.parse(src)
        except Exception:
            continue

        lines = src.splitlines()
        changed_fields = []
        for node in tree.body:
            if not isinstance(node, _ast.ClassDef):
                continue
            model = next((m for m in models if node.name.lower().startswith(m.lower())), None)
            if not model:
                continue
            for child in node.body:
                if not (isinstance(child, _ast.AnnAssign) and isinstance(child.target, _ast.Name)):
                    continue
                field = child.target.id
                if models[model].get(field) is not True:
                    continue  # column not nullable (or doesn't exist) — leave alone
                if _is_optional_annotation(child.annotation):
                    continue
                if child.lineno != child.end_lineno:
                    continue  # multi-line annotation — too risky to rewrite textually
                ann_src = _ast.get_source_segment(src, child.annotation) or ""
                if not ann_src:
                    continue
                i = child.lineno - 1
                indent = lines[i][:len(lines[i]) - len(lines[i].lstrip())]
                if child.value is not None:
                    val_src = _ast.get_source_segment(src, child.value) or "None"
                    # Field(..., ...) keeps the field pydantic-required even
                    # with an Optional annotation — swap the Ellipsis default.
                    val_src = re.sub(r"^Field\(\s*\.\.\.", "Field(None", val_src)
                    lines[i] = f"{indent}{field}: Optional[{ann_src}] = {val_src}"
                else:
                    lines[i] = f"{indent}{field}: Optional[{ann_src}] = None"
                changed_fields.append(f"{node.name}.{field}")

        if not changed_fields:
            continue

        new_src = "\n".join(lines) + ("\n" if src.endswith("\n") else "")
        # Make sure Optional is importable
        if not re.search(r"^from typing import [^\n]*\bOptional\b", new_src, re.MULTILINE):
            m = re.search(r"^from typing import ([^\n]+)$", new_src, re.MULTILINE)
            if m:
                new_src = new_src[:m.start(1)] + m.group(1).rstrip() + ", Optional" + new_src[m.end(1):]
            else:
                new_src = "from typing import Optional\n" + new_src

        sf.write_text(new_src, encoding="utf-8")
        patched += 1
        print(f"  [patcher] Made nullable-column schema fields Optional in {sf.name}: "
              f"{', '.join(changed_fields[:5])}")

    return patched


_DT_COLUMN_TYPES = {"DateTime": "datetime", "Date": "date", "Time": "time"}


def _patch_response_schema_id_and_datetimes(project_path: Path) -> int:
    """
    Two guaranteed-broken response patterns, fixed deterministically:

    1. A response-ish schema (used as response_model, or named *Response/*Out/
       *Read) with NO `id` field — FastAPI strips id from every response, the
       CRUD journey can't capture an entity_id, and edit/delete/persistence
       all fail ("201 id=None"). Inject `id: Optional[int] = None`.

    2. A schema field annotated str/Optional[str] whose model column is
       DateTime/Date/Time — the ORM hands pydantic a datetime object, raising
       ResponseValidationError (500) on every row returned. Retype to the
       real datetime type (pydantic still accepts ISO strings on input).
    """
    import ast as _ast
    from app.services.schema_model_validator import _collect_response_model_schemas

    models_dir = project_path / "app" / "models"
    schemas_dir = project_path / "app" / "schemas"
    if not models_dir.exists() or not schemas_dir.exists():
        return 0

    # ModelName -> {column: sqlalchemy type name}
    model_cols: dict[str, dict] = {}
    for mf in models_dir.glob("*.py"):
        try:
            tree = _ast.parse(mf.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        for node in _ast.walk(tree):
            if not isinstance(node, _ast.ClassDef):
                continue
            cols = {}
            for child in node.body:
                if (isinstance(child, _ast.Assign) and isinstance(child.value, _ast.Call)
                        and getattr(child.value.func, "id", "") == "Column"):
                    tname = ""
                    if child.value.args:
                        a0 = child.value.args[0]
                        if isinstance(a0, _ast.Name):
                            tname = a0.id
                        elif isinstance(a0, _ast.Call) and isinstance(a0.func, _ast.Name):
                            tname = a0.func.id
                    for t in child.targets:
                        if isinstance(t, _ast.Name):
                            cols[t.id] = tname
            if cols:
                model_cols[node.name] = cols

    if not model_cols:
        return 0

    response_schemas = _collect_response_model_schemas(str(project_path))
    patched = 0

    for sf in schemas_dir.glob("*.py"):
        if sf.name == "__init__.py":
            continue
        try:
            src = sf.read_text(encoding="utf-8", errors="replace")
            tree = _ast.parse(src)
        except Exception:
            continue

        lines = src.splitlines()
        inserts: list[tuple[int, str]] = []
        changes: list[str] = []
        need_dt: set = set()
        need_optional = False

        for node in tree.body:
            if not isinstance(node, _ast.ClassDef) or not node.body:
                continue
            model = next((m for m in model_cols if node.name.lower().startswith(m.lower())), None)
            if not model:
                continue
            cols = model_cols[model]
            fields = {c.target.id: c for c in node.body
                      if isinstance(c, _ast.AnnAssign) and isinstance(c.target, _ast.Name)}

            is_responseish = (node.name in response_schemas
                              or re.search(r"(Response|Out|Read)$", node.name))
            if is_responseish and "id" in cols and "id" not in fields:
                first = node.body[0]
                if (isinstance(first, _ast.Expr) and isinstance(first.value, _ast.Constant)
                        and isinstance(first.value.value, str)):
                    # skip past the docstring
                    insert_at = first.end_lineno
                else:
                    insert_at = first.lineno - 1
                inserts.append((insert_at, "    id: Optional[int] = None"))
                need_optional = True
                changes.append(f"{node.name}.id")

            for fname, child in fields.items():
                py_dt = _DT_COLUMN_TYPES.get(cols.get(fname, ""))
                if not py_dt or child.lineno != child.end_lineno:
                    continue
                ann = _ast.get_source_segment(src, child.annotation) or ""
                if ann == "str":
                    new_ann = py_dt
                elif ann == "Optional[str]":
                    new_ann = f"Optional[{py_dt}]"
                else:
                    continue
                i = child.lineno - 1
                lines[i] = lines[i].replace(f": {ann}", f": {new_ann}", 1)
                need_dt.add(py_dt)
                changes.append(f"{node.name}.{fname}:{py_dt}")

        if not changes:
            continue

        for idx, text in sorted(inserts, reverse=True):
            lines.insert(idx, text)
        new_src = "\n".join(lines) + ("\n" if src.endswith("\n") else "")

        if need_dt:
            m = re.search(r"^from datetime import ([^\n]+)$", new_src, re.MULTILINE)
            if m:
                existing = {n.strip() for n in m.group(1).split(",")}
                missing = sorted(need_dt - existing)
                if missing:
                    new_src = (new_src[:m.end(1)] + ", " + ", ".join(missing) + new_src[m.end(1):])
            else:
                new_src = f"from datetime import {', '.join(sorted(need_dt))}\n" + new_src
        if need_optional and not re.search(r"^from typing import [^\n]*\bOptional\b", new_src, re.MULTILINE):
            m = re.search(r"^from typing import ([^\n]+)$", new_src, re.MULTILINE)
            if m:
                new_src = new_src[:m.start(1)] + m.group(1).rstrip() + ", Optional" + new_src[m.end(1):]
            else:
                new_src = "from typing import Optional\n" + new_src

        sf.write_text(new_src, encoding="utf-8")
        patched += 1
        print(f"  [patcher] Response schema id/datetime fixes in {sf.name}: "
              f"{', '.join(changes[:6])}")

    return patched


# Curated set of REAL lucide-react icon names the LLM commonly uses. Only names
# in this set are ever auto-added to an import, so the patcher can never
# introduce a non-existent export that would break the vite build.
_LUCIDE_ICONS = frozenset("""
Plus Minus X Check CheckCircle CheckCircle2 CheckSquare Circle XCircle Search
Filter SlidersHorizontal ChevronRight ChevronLeft ChevronDown ChevronUp
ChevronsRight ChevronsLeft ChevronsUpDown ArrowRight ArrowLeft ArrowUp ArrowDown
ArrowUpRight ArrowDownRight Menu Home User Users UserPlus UserCircle UserCircle2
UserCheck LogOut LogIn Settings Settings2 Bell BellOff Calendar CalendarDays
CalendarCheck CalendarClock Clock Clock3 AlertCircle AlertTriangle Info
HelpCircle Trash Trash2 Edit Edit2 Edit3 Pencil PenTool PenLine Save Copy
Clipboard ClipboardCheck ClipboardList Download Upload Share Share2 Eye EyeOff
Lock Unlock Mail MailOpen Phone MapPin Map Navigation Compass Star Heart
Bookmark Tag Tags Flag Folder FolderOpen FolderPlus File FileText FilePlus
FileCheck Image ImagePlus Video Music List ListTodo ListChecks ListFilter
Grid Grid2x2 Grid3x3 LayoutDashboard LayoutGrid LayoutList Layers Package
PackageOpen ShoppingCart ShoppingBag CreditCard DollarSign Wallet Receipt Coins
Banknote TrendingUp TrendingDown BarChart BarChart2 BarChart3 BarChart4
PieChart LineChart AreaChart Activity Zap Award Target Flame Trophy Medal
Rocket Lightbulb Sun Moon Cloud CloudRain Droplet Wind Umbrella Thermometer
RefreshCw RefreshCcw RotateCw RotateCcw Repeat Repeat2 Shuffle MoreHorizontal
MoreVertical ExternalLink Link Link2 Paperclip Send SendHorizontal MessageCircle
MessageSquare Smile ThumbsUp ThumbsDown Play Pause Square StopCircle PlayCircle
PauseCircle Loader Loader2 LoaderCircle Sparkles Gift Coffee Book BookOpen
BookMarked Briefcase Building Building2 Globe Globe2 Wifi WifiOff Battery Camera
Mic MicOff Volume2 Volume1 VolumeX Power Palette Grip GripVertical GripHorizontal
Dot Hash AtSign Percent Timer Watch Sunrise Sunset Maximize Maximize2 Minimize
Minimize2 ZoomIn ZoomOut Move Crosshair Key KeyRound Shield ShieldCheck
ShieldAlert ShieldX Fingerprint Scan QrCode Calculator Archive ArchiveRestore
Inbox Undo Undo2 Redo Redo2 Printer Scissors Type Bold Italic Underline
AlignLeft AlignCenter AlignRight AlignJustify Quote Code Code2 Terminal Command
Cpu Database Server HardDrive Monitor Smartphone Tablet Laptop Headphones
Speaker Radio Tv Gamepad Gamepad2 Puzzle Component Dumbbell Apple Utensils
Pizza Wine Beer Cake Carrot Salad Soup Egg Fish Beef Sandwich IceCream Cookie
Milk CupSoda Croissant Donut Popcorn Cherry Grape Banana Bird Cat Dog Rabbit
Leaf Trees TreePine Flower Flower2 Sprout Bug Sun Snowflake CloudSun CloudMoon
Star Bookmark Heart HeartPulse Stethoscope Pill Syringe Activity Brain Bone Eye
Ear Hand Footprints Baby Accessibility Bike Car Bus Train Plane Ship Truck
Anchor Fuel ParkingCircle TrafficCone Construction Wrench Hammer Ruler Scale
Paintbrush Brush Eraser Highlighter Feather Pin PinOff Bookmark Sticker Note
StickyNote NotebookPen Notebook Newspaper Rss Podcast Mic2 Music2 Music3 Music4
Disc Disc2 Disc3 Radio Cast Airplay Bluetooth Cable Plug Plug2 PlugZap Usb
""".split())


def _module_dotted(project_root: Path, py_file: Path) -> str | None:
    """app/routes/auth_routes.py -> 'app.routes.auth_routes' (None if outside app/)."""
    try:
        rel = py_file.relative_to(project_root)
    except ValueError:
        return None
    parts = rel.with_suffix("").parts
    if not parts or parts[0] != "app":
        return None
    return ".".join(parts)


def _backend_module_exists(project_root: Path, dotted: str) -> bool:
    segments = dotted.split(".")
    # app.routes.auth_routes -> app/routes/auth_routes.py  OR  app/routes/auth_routes/__init__.py
    mod = project_root / Path(*segments).with_suffix(".py")
    pkg = project_root / Path(*segments) / "__init__.py"
    return mod.exists() or pkg.exists()


def _patch_redirect_missing_backend_imports(project_path: Path) -> int:
    """Redirect `from app.X import ...` when app/X doesn't exist but the imported
    symbols live in a real module elsewhere.

    The fix-loop LLM habitually rewrites main.py to
    `from app.routers.auth import auth_router` or `from app.api.seed import ...`
    when the real modules are `app/routes/auth_routes.py` etc. That's a hard
    ModuleNotFoundError at startup — the backend never boots, so auth 404s and
    every fix attempt regresses and reverts (seen on habit_forge: stuck at 75,
    'No module named app.routers'). This is the backend twin of the frontend
    missing-import scaffold. We resolve it by pointing the import at the module
    that actually defines the symbols; if none is found, a re-export shim is
    created so the import at least resolves."""
    import re
    root = project_path.resolve()
    app_dir = root / "app"
    if not app_dir.exists():
        return 0

    py_files = [p for p in app_dir.rglob("*.py") if "__pycache__" not in p.parts]

    # Symbol index: top-level name -> [dotted module paths that define it]
    sym_index: dict[str, list[str]] = {}
    def_re = re.compile(r"^(?:def|class)\s+(\w+)", re.MULTILINE)
    assign_re = re.compile(r"^(\w+)\s*=", re.MULTILINE)
    for pf in py_files:
        dotted = _module_dotted(root, pf)
        if not dotted:
            continue
        try:
            src = pf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for name in set(def_re.findall(src)) | set(assign_re.findall(src)):
            sym_index.setdefault(name, [])
            if dotted not in sym_index[name]:
                sym_index[name].append(dotted)

    import_re = re.compile(r"^from\s+app\.([\w.]+)\s+import\s+([^\n(]+)", re.MULTILINE)
    patched = 0

    for pf in py_files:
        try:
            src = pf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        original = src

        def _fix(m: re.Match) -> str:
            dotted = m.group(1)
            names_raw = m.group(2).split("#")[0]  # drop any inline comment
            if _backend_module_exists(root, f"app.{dotted}"):
                return m.group(0)  # target exists — leave it
            specs = [n.strip() for n in names_raw.split(",") if n.strip() and n.strip() != "*"]
            base_names = [s.split(" as ")[0].strip() for s in specs]
            if not base_names:
                return m.group(0)
            # Find a real module defining the MOST of the imported names.
            candidates: dict[str, int] = {}
            for n in base_names:
                for modpath in sym_index.get(n, []):
                    if modpath == f"app.{dotted}":
                        continue
                    candidates[modpath] = candidates.get(modpath, 0) + 1
            if not candidates:
                return m.group(0)  # can't resolve here; shim pass handles it
            best = max(candidates, key=lambda k: candidates[k])
            # Only redirect if the best module covers all names (safe, unambiguous).
            if candidates[best] < len(base_names):
                return m.group(0)
            return f"from {best} import {', '.join(specs)}"

        src = import_re.sub(_fix, src)
        if src != original:
            try:
                pf.write_text(src, encoding="utf-8")
                patched += 1
                print(f"  [patcher] Redirected missing backend import(s) in {pf.name}")
            except Exception:
                pass

    # Shim pass: any still-missing `from app.X import ...` where we couldn't
    # redirect — create a re-export shim module so startup doesn't crash.
    for pf in py_files:
        try:
            src = pf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for m in import_re.finditer(src):
            dotted = m.group(1)
            if _backend_module_exists(root, f"app.{dotted}"):
                continue
            names = [n.strip().split(" as ")[0].strip()
                     for n in m.group(2).split("#")[0].split(",") if n.strip() and n.strip() != "*"]
            # Resolve each name to a real module for the shim body.
            shim_lines = ["# Auto-generated shim: re-exports symbols from their real modules.\n"]
            resolved_any = False
            for n in names:
                mods = [x for x in sym_index.get(n, []) if x != f"app.{dotted}"]
                if mods:
                    shim_lines.append(f"from {mods[0]} import {n}\n")
                    resolved_any = True
            if not resolved_any:
                continue
            segments = dotted.split(".")
            shim_path = root / Path("app", *segments).with_suffix(".py")
            if shim_path.exists():
                continue
            try:
                shim_path.parent.mkdir(parents=True, exist_ok=True)
                # ensure package __init__.py files exist along the path
                pkg = root / "app"
                for seg in segments[:-1]:
                    pkg = pkg / seg
                    init = pkg / "__init__.py"
                    if not init.exists():
                        init.write_text("", encoding="utf-8")
                shim_path.write_text("".join(shim_lines), encoding="utf-8")
                patched += 1
                print(f"  [patcher] Created re-export shim app/{'/'.join(segments)}.py")
            except Exception:
                pass

    return patched


_STATUS_PARAGRAPH_RE = re.compile(r"\{status && <p[^>]*>\{status\}</p>\}\n?[ \t]*")


def _patch_hidden_loading_status(project_path: Path) -> int:
    """Hoist a retry/wake-up status message out of the *not-loading* branch.

    Generated pages that poll a cold-starting backend follow a 3-attempt
    retry loop with a `status` message ("Waking up the server... retrying
    (2/3)") -- but the full-page skeleton pattern renders it only inside
    `{loading ? (<skeleton/>) : (... {status && <p>{status}</p>} ...)}`.
    Since `status` is set *while* `loading` is still true, the message is
    invisible for the entire retry window it exists to explain: the user
    just sees a bare gray skeleton with no indication anything is happening
    (reported live on habit-forge's dashboard). Move it so it renders
    unconditionally, above the ternary.
    """
    src_dir = project_path / "src"
    if not src_dir.exists():
        return 0

    patched = 0
    for jf in src_dir.rglob("*.jsx"):
        try:
            content = jf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if "{status && <p" not in content or "{loading ? (" not in content:
            continue

        m = _STATUS_PARAGRAPH_RE.search(content)
        if not m:
            continue
        status_block = m.group(0).rstrip()
        without = content[: m.start()] + content[m.end():]
        idx = without.find("{loading ? (")
        if idx == -1:
            continue
        new_content = without[:idx] + status_block + "\n      " + without[idx:]

        if new_content != content:
            jf.write_text(new_content, encoding="utf-8")
            patched += 1
            print(f"  [patcher] Hoisted retry-status message above the loading skeleton in {jf.name}")

    return patched


_AUTH_USERNAME_PAIR_RE = re.compile(
    r"([\w.]+)\.username([^\n]{0,60}localStorage\.setItem\(\s*['\"]display_name['\"]\s*,\s*)\1\.username"
)
_AUTH_ID_PAIR_RE = re.compile(
    r"([\w.]+)\.id([^\n]{0,60}localStorage\.setItem\(\s*['\"]user_id['\"]\s*,\s*(?:String\()?)\1\.id"
)


def _patch_frontend_auth_field_names(project_path: Path) -> int:
    """Fix LoginPage/RegisterPage reading `.username`/`.id` off the auth response.

    The backend's known-good injected app/routes/auth_routes.py (_patch_auth_routes,
    below) always responds to /auth/login and /auth/register with
    {access_token, token_type, user_id, email, display_name} -- there is no
    `username` or `id` field, ever. LLM-generated auth pages routinely assume
    the generic {id, username} shape anyway, so
    `if (res.data.username) localStorage.setItem('display_name', res.data.username)`
    silently never fires (the guard is always falsy) and the dashboard falls
    back to "Hello, User!" forever, with user_id never stored either. Scoped
    to the exact guard+setItem pair so it can't touch an unrelated `.id`/
    `.username` elsewhere in the file.
    """
    src_dir = project_path / "src"
    if not src_dir.exists():
        return 0

    patched = 0
    for jf in src_dir.rglob("*.jsx"):
        try:
            content = jf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if "/auth/login" not in content and "/auth/register" not in content:
            continue

        new_content = _AUTH_USERNAME_PAIR_RE.sub(
            lambda m: f"{m.group(1)}.display_name{m.group(2)}{m.group(1)}.display_name",
            content,
        )
        new_content = _AUTH_ID_PAIR_RE.sub(
            lambda m: f"{m.group(1)}.user_id{m.group(2)}{m.group(1)}.user_id",
            new_content,
        )

        if new_content != content:
            jf.write_text(new_content, encoding="utf-8")
            patched += 1
            print(f"  [patcher] Fixed auth response field names (username -> display_name, id -> user_id) in {jf.name}")

    return patched


_SIGNUP_HASHED_PW_KEY_RE = re.compile(r"\bhashed_password(\s*:)")


def _patch_frontend_signup_password_key(project_path: Path) -> int:
    """Fix RegisterPage sending `hashed_password` instead of `password`.

    The known-good SignupRequest {email, password, display_name} takes the
    plaintext password and hashes it server-side -- the client never has a
    hash to send. LLMs occasionally name the payload key after the backend's
    storage concept anyway: `API.post('/auth/register', { email,
    hashed_password: password, display_name })`. The backend then sees no
    `password` field at all and 422s with a bare "Field required" (no field
    name surfaced to the user), so every registration hangs on "Creating
    account..." and then fails. Reproduced live on forge_expense_tracker: the
    adjacent /auth/login call in the same function correctly used
    `password: n` for the identical variable, confirming this is a naming
    slip rather than an intentional different contract.
    """
    src_dir = project_path / "src"
    if not src_dir.exists():
        return 0

    patched = 0
    for jf in src_dir.rglob("*.jsx"):
        try:
            content = jf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if "/auth/register" not in content and "/auth/signup" not in content:
            continue
        if "hashed_password" not in content:
            continue

        new_content = _SIGNUP_HASHED_PW_KEY_RE.sub(r"password\1", content)
        if new_content != content:
            jf.write_text(new_content, encoding="utf-8")
            patched += 1
            print(f"  [patcher] Fixed signup payload key hashed_password -> password in {jf.name}")

    return patched


_STALE_STATUS_ON_ERROR_RE = re.compile(
    r"if\s*\(\s*(\w+)\s*\)\s*\{\s*setError\(\1\);\s*setLoading\(false\);\s*return;\s*\}"
)


def _patch_stale_status_on_error(project_path: Path) -> int:
    """Clear the in-flight retry-status message when a real error occurs.

    The standard generated retry-loop idiom (Login/Register/Dashboard/list
    pages) is:
        const msg = parseError(err);
        if (msg) { setError(msg); setLoading(false); return; } // real API error, don't retry
        if (attempt < 3) { setStatus(`...retrying...`); await sleep(...); }
    The error branch never clears `status`, so whatever transient message was
    showing ("Creating account...", "Waking up the server...") stays on
    screen forever, stacked right next to the real error -- reported live as
    "Field required" shown together with a permanent "Creating account...".
    Purely cosmetic (the real error is still shown), but confusing enough to
    look like the page is stuck. Scoped to files that actually declare a
    `setStatus` setter, so it can't insert a call to something undefined.
    """
    src_dir = project_path / "src"
    if not src_dir.exists():
        return 0

    patched = 0
    for jf in src_dir.rglob("*.jsx"):
        try:
            content = jf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if "setStatus(" not in content:
            continue

        def _fix(m: re.Match) -> str:
            var = m.group(1)
            return (
                f"if ({var}) {{ setError({var}); setStatus(null); "
                f"setLoading(false); return; }}"
            )

        new_content = _STALE_STATUS_ON_ERROR_RE.sub(_fix, content)
        if new_content != content:
            jf.write_text(new_content, encoding="utf-8")
            patched += 1
            print(f"  [patcher] Cleared stale status message on error path in {jf.name}")

    return patched


def _patch_missing_icon_imports(project_path: Path) -> int:
    """Add lucide-react icons that are USED in JSX but never imported.

    LLMs routinely render <ChevronRight/> (or another icon) without importing it.
    The vite build passes — an undefined JSX identifier is valid *syntax* — but at
    runtime it's a ReferenceError that unmounts React and shows a BLANK PAGE.
    Nothing else in the pipeline catches this (build is green, the page just
    silently dies). Seen live: /tasks rendered blank because ChevronRight wasn't
    imported. This adds any such icon (restricted to the known-real _LUCIDE_ICONS
    set, so it can never introduce a bad export) to the file's lucide import,
    creating the import line if the file has none.
    """
    import re
    src_dir = project_path / "src"
    if not src_dir.exists():
        return 0

    patched = 0
    lucide_import_re = re.compile(r"import\s*\{([^}]*)\}\s*from\s*['\"]lucide-react['\"]\s*;?")

    for jf in src_dir.rglob("*.jsx"):
        try:
            src = jf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        # Names already imported (any import) or defined locally as components.
        known: set[str] = set()
        for m in re.finditer(r"import\s+(?:(\w+)\s*,?\s*)?(?:\{([^}]*)\})?\s*from", src):
            if m.group(1):
                known.add(m.group(1))
            if m.group(2):
                for n in m.group(2).split(","):
                    n = n.strip().split(" as ")[-1].strip()
                    if n:
                        known.add(n)
        for m in re.finditer(r"(?:const|function|let|class)\s+([A-Z]\w+)", src):
            known.add(m.group(1))

        used = set(re.findall(r"<([A-Z]\w+)[\s/>]", src))
        missing = sorted((used - known) & _LUCIDE_ICONS)
        if not missing:
            continue

        m = lucide_import_re.search(src)
        if m:
            existing = [n.strip() for n in m.group(1).split(",") if n.strip()]
            merged = existing + [n for n in missing if n not in existing]
            new_import = "import { " + ", ".join(merged) + " } from 'lucide-react';"
            src = src[:m.start()] + new_import + src[m.end():]
        else:
            # No lucide import in this file — add one after the first import line
            # (or at the very top if there are none).
            new_import = "import { " + ", ".join(missing) + " } from 'lucide-react';\n"
            first_import = re.search(r"^import .*\n", src, re.MULTILINE)
            if first_import:
                src = src[:first_import.end()] + new_import + src[first_import.end():]
            else:
                src = new_import + src

        try:
            jf.write_text(src, encoding="utf-8")
            patched += 1
            print(f"  [patcher] Added missing icon import(s) {missing} to {jf.name}")
        except Exception:
            pass

    return patched


def run_frontend_patches(project_path: Path) -> int:
    """
    Every frontend-only deterministic patch, in one place.

    This is the single list new frontend patchers get registered in. It's
    called both from run_deterministic_patches (full generation) and
    standalone from main.py's _resync_frontend (the "Check & Fix deployed
    app" frontend resync, which never touches the backend and can't re-run
    the full pipeline). Those two call sites drifted apart once already:
    _resync_frontend hardcoded two patcher names directly and silently
    stopped picking up every frontend patcher added afterward (the
    hashed_password signup-payload fix and the stale-status fix never
    reached a live "Check & Fix" resync because of it). Routing both
    call sites through this one function makes that class of bug
    structurally impossible going forward.
    """
    patched = 0
    patched += bool(_patch_frontend_package_json(project_path))
    patched += _patch_missing_icon_imports(project_path)
    patched += _patch_frontend_auth_field_names(project_path)
    patched += _patch_frontend_signup_password_key(project_path)
    patched += _patch_stale_status_on_error(project_path)
    patched += _patch_hidden_loading_status(project_path)
    patched += bool(_patch_pagination_component(project_path))
    patched += patch_ensure_auth_pages(project_path)
    return patched


def run_deterministic_patches(project_path: str, skip_protected_injections: bool = False) -> int:
    """
    Run all deterministic patches on a generated project.
    Returns the number of files modified.

    skip_protected_injections=True: skip auth_routes.py and auth_utils.py injection.
    Pass True when calling after Architecture Repair so the repair's output is not
    overwritten by the static template.
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
        patched = _patch_wrong_auth_module(patched)
        patched = _patch_passlib(patched)
        patched = _patch_pydantic_regex(patched)
        patched = _patch_func_name_vs_label(patched)
        patched = _patch_pydantic_orm_mode(patched)
        patched = _patch_async_sync(patched, filepath=rel)
        patched = _patch_circular_schema_imports(patched, filepath=rel)
        patched = _patch_depends_body(patched, rel)
        patched = _patch_from_orm(patched)
        patched = _patch_orm_response_model(patched, rel, project_path=root)

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

    # Deduplicate model files (user.py + users.py both having class User → keep larger)
    _patch_deduplicate_models(root)

    # Same collision for schemas (expense.py + expenses.py both having
    # ExpenseCreate → keep larger). Must run before the import-redirect patcher
    # so any import left pointing at the dropped file gets resolved to the kept one.
    _patch_deduplicate_schemas(root)

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

    # Auth utils / routes: inject known-good templates on initial generation.
    # skip_protected_injections=True when called after Architecture Repair — the repair's
    # output is authoritative and must not be clobbered by the static template.
    if not skip_protected_injections:
        _patch_auth_utils(root)
        _patch_auth_requirements(root)
        _patch_auth_routes(root)

    # Redirect `from app.routers.auth import ...` (and similar wrong paths) to the
    # module that actually defines the symbols — must run before router wiring so
    # main.py's imports resolve. Prevents the ModuleNotFoundError that made auth
    # 404 and every fix attempt regress-and-revert.
    _patch_redirect_missing_backend_imports(root)

    # Wire ALL routers into main.py — runs after auth_routes injection so auth_router
    # is already in main.py; this catches every other generated router.
    _patch_wire_orphan_routers(root)

    # Synthesize LoginPage/RegisterPage if App.jsx redirects to /login but
    # the LLM never generated them -- must run BEFORE the generic orphan
    # route wirer below, which would otherwise wrap these in PrivateRoute.
    patch_ensure_auth_pages(root)

    # Frontend mirror: wire any page component App.jsx imports but never
    # mounted on a <Route> (see docstring for why this is invisible to
    # every other automated check).
    _patch_wire_orphan_frontend_routes(root)

    # Seed robustness: guard against IndexError when parent entity inserts fail
    _patch_seed_robustness(root)

    # Create stub schema files for any route imports that point to missing modules
    # (common after architecture repair generates new route files)
    _patch_create_missing_schemas(root)

    # Make Response schema fields Optional so ORM field-name mismatches don't crash
    # (e.g. UserResponse.username required but User model uses email only)
    _patch_response_schemas_optional(root)

    # Required schema fields on nullable model columns → Optional[T] = None
    # (kills the recurring "required but model allows NULL" validator error)
    _patch_schema_nullable_required_mismatch(root)

    # Response schemas must expose id (journey/frontends need it) and must
    # type DateTime columns as datetime, not str (else 500 on every row)
    _patch_response_schema_id_and_datetimes(root)

    # Inject model_config = {'from_attributes': True} into all Pydantic schemas
    # so FastAPI can serialize SQLAlchemy ORM objects returned from route handlers
    _patch_schemas_from_attributes(root)

    # Filter **schema.dict() unpacking to remove fields not on the SQLAlchemy model.
    # Prevents TypeError: 'status' is an invalid keyword argument for Task when the
    # Pydantic schema has extra fields that don't exist as columns on the model.
    _patch_star_dict_extra_fields(root)

    # Fix the same filter if it was already generated with the older, unsafe
    # hasattr(Model, k) form -- passes a read-only @property through and
    # raises AttributeError: property 'x' has no setter on every create request.
    _patch_unsafe_model_hasattr_filter(root)

    # Fix an already-filtered constructor call missing the exclusion for a
    # trailing kwarg that collides with a same-named schema field (e.g.
    # HabitCreate.user_id vs the route's own user_id=current_user.id) --
    # "got multiple values for keyword argument" on every create request.
    _patch_filtered_ctor_kwarg_collision(root)

    # Fix attribute accesses (e.g. user.username when model only has email).
    # The field_patcher fixes constructor calls; this fixes dict literals and returns.
    _patch_attr_access_mismatches(root)

    # Fix SQLAlchemy ORM models used as Pydantic field types in route files
    # (e.g. labels: List[Label] where Label is a SQLAlchemy model → List[Any])
    _patch_orm_type_in_route_schemas(root)

    # Ensure all schema files that use BaseModel/Field/Optional actually import them.
    _patch_missing_pydantic_imports(root)

    # Fix response_model=List[X] on handlers that return {"items": ..., "total": N}
    # → strip the List[] response_model so FastAPI passes the dict through unvalidated.
    _patch_list_response_model_mismatch(root)

    # Create service stubs when route files import from app.services.X that doesn't exist.
    # LLMs sometimes generate a service layer but only generate routes, not services.
    _patch_create_missing_service_stubs(root)

    # Inject db.refresh(obj) after db.commit() where missing — LLMs forget this, causing
    # POST handlers to return id=None because the ORM object isn't re-bound to the DB row.
    _patch_missing_db_refresh(root)

    # All frontend-only fixes live in one bundle (see run_frontend_patches
    # below) so a standalone frontend resync (main.py's _resync_frontend,
    # used by "Check & Fix deployed app") can never silently drift out of
    # sync with this list again.
    run_frontend_patches(root)

    if modified:
        print(f"  [patcher] Patched {modified} file(s) — passlib→bcrypt, async→sync, smart quotes")

    return modified
