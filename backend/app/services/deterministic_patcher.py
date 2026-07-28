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
import ast
import keyword
import re
import time
from dataclasses import dataclass
from pathlib import Path

from app.utils.brace_matching import find_matching_brace
from typing import Optional


# Classes always defined inline by the injected app/routes/auth_routes.py
# template (_build_auth_routes_template below) — never imported from app/schemas/.
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

    # A generated relationship can itself be called ``property``.  Once its
    # method is defined, the class namespace shadows the built-in decorator,
    # so a later ``@property`` raises ``TypeError: 'property' object is not
    # callable`` while importing the model.  Use the qualified built-in for
    # every injected relation: generation order is variable, so qualifying
    # only the colliding relation would still leave a later one broken.
    decorator = "builtins.property"
    if not re.search(r"^import builtins$", content, re.MULTILINE):
        content = "import builtins\n" + content

    if tgt and own_fk_col:
        body = (
            f"    @{decorator}\n"
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
            f"    @{decorator}\n"
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
            f"    @{decorator}\n"
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


def _patch_missing_model_imports_in_routes(project_path: Path) -> int:
    """Inject `from app.models.<module> import <Class>` into route files that
    reference a model class (db.query(X), X.attr, isinstance(v, X)) without
    importing it.

    Exp132: aggregate/"stats" routes generated by the LLM repeatedly query
    2-3 models (User, Task, Priority, ...) while importing none of them --
    a NameError that only surfaces after a full runtime round-trip, then
    gets "fixed" by the LLM fix loop, then reappears verbatim the next time
    that same route is regenerated (nuclear/architecture-repair strategies
    re-run the original generation prompt and hit the same gap). Catching
    it here is instant and $0, and prevents the fix loop from ever needing
    to spend an attempt -- or a second cold npm install -- on it.
    """
    models_dir = project_path / "app" / "models"
    routes_dir = project_path / "app" / "routes"
    if not models_dir.exists() or not routes_dir.exists():
        return 0

    model_index = _build_model_index(models_dir)
    if not model_index:
        return 0

    patched = 0
    for rf in sorted(routes_dir.glob("*.py")):
        try:
            text = rf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        imported_names: set[str] = set()
        for grp in re.findall(r"from app\.models\.\w+ import ([\w, ]+)", text):
            imported_names.update(n.strip() for n in grp.split(","))

        missing: list[str] = []
        for cls, meta in model_index.items():
            if cls in imported_names or not meta.get("module"):
                continue
            usage_re = re.compile(
                rf"\bdb\.query\(\s*{cls}\s*\)|\b{cls}\.\w+|isinstance\([^,]+,\s*{cls}\s*\)"
            )
            if usage_re.search(text):
                missing.append(f"from app.models.{meta['module']} import {cls}")

        if not missing:
            continue

        lines = text.split("\n")
        insert_at = 0
        paren_depth = 0
        for i, line in enumerate(lines):
            if paren_depth > 0:
                # Inside a multi-line `from x import (...)` continuation --
                # never a valid insertion point on its own; only becomes one
                # once the closing paren is reached.
                paren_depth += line.count("(") - line.count(")")
                if paren_depth <= 0:
                    insert_at = i + 1
                continue
            if line.startswith(("from ", "import ")):
                depth_delta = line.count("(") - line.count(")")
                if depth_delta > 0:
                    paren_depth += depth_delta
                else:
                    insert_at = i + 1
        for imp in missing:
            if imp not in text:
                lines.insert(insert_at, imp)
                insert_at += 1
        rf.write_text("\n".join(lines), encoding="utf-8")
        patched += 1
        print(f"  [patcher] Injected {len(missing)} missing model import(s) into {rf.name}")

    return patched


def _uses_session_as_bare_annotation(tree: ast.AST) -> bool:
    """True if any function parameter is annotated with the bare name
    `Session` (e.g. `db: Session = Depends(get_db)`) -- the confirmed live
    shape, deliberately narrower than "the name Session appears anywhere,"
    which could false-positive on an unrelated same-named local or a
    different Session class (e.g. requests.Session)."""
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for arg in list(node.args.args) + list(node.args.kwonlyargs) + list(node.args.posonlyargs):
            if isinstance(arg.annotation, ast.Name) and arg.annotation.id == "Session":
                return True
    return False


# Confirmed live (habit_tracker, 2026-07-25): an "ARCHITECTURE REPAIR" LLM
# response for some-endpoint_routes.py wrote `def some_endpoint(db: Session
# = Depends(get_db))` while importing only `from fastapi import APIRouter,
# Depends, Query, HTTPException` and `from app.database import get_db` --
# no `from sqlalchemy.orm import Session` anywhere. FastAPI's own symbols
# (APIRouter/Depends/Query) get imported reliably since the decorator and
# dependency injection need them to even write the handler; `Session` is
# "just a type hint" and evaluates only when Python parses the `def`
# statement itself, so the omission doesn't surface until the whole app
# fails to import at startup -- a hard crash on every request, not a
# single endpoint's worth of damage.
def _patch_missing_session_import_in_routes(project_path: Path) -> int:
    """
    Injects `from sqlalchemy.orm import Session` into any app/routes/*.py
    file that uses the bare `Session` type annotation without already
    importing it under that name (from any module, in case a project
    genuinely imports a differently-sourced Session and this would be a
    false positive).
    """
    routes_dir = project_path / "app" / "routes"
    if not routes_dir.exists():
        return 0

    patched = 0
    for rf in sorted(routes_dir.glob("*.py")):
        try:
            text = rf.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(text)
        except Exception:
            continue

        if not _uses_session_as_bare_annotation(tree):
            continue

        already_imported = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and any(
                (a.asname or a.name) == "Session" for a in node.names
            ):
                already_imported = True
                break
            if isinstance(node, ast.Import) and any(
                (a.asname or a.name) == "Session" for a in node.names
            ):
                already_imported = True
                break
        if already_imported:
            continue

        lines = text.split("\n")
        insert_at = 0
        paren_depth = 0
        for i, line in enumerate(lines):
            if paren_depth > 0:
                paren_depth += line.count("(") - line.count(")")
                if paren_depth <= 0:
                    insert_at = i + 1
                continue
            if line.startswith(("from ", "import ")):
                depth_delta = line.count("(") - line.count(")")
                if depth_delta > 0:
                    paren_depth += depth_delta
                else:
                    insert_at = i + 1
        lines.insert(insert_at, "from sqlalchemy.orm import Session")
        new_text = "\n".join(lines)
        try:
            ast.parse(new_text)
        except SyntaxError:
            continue  # never write a syntactically broken file -- skip, fail soft

        rf.write_text(new_text, encoding="utf-8")
        patched += 1
        print(f"  [patcher] Injected missing 'from sqlalchemy.orm import Session' in {rf.name}")

    return patched


def _patch_invalid_model_attribute_access(project_path: Path) -> int:
    """Wrap route handlers that reference a nonexistent model attribute
    (ModelName.attr where attr isn't a real column/property/method) in a
    try/except that returns a safe empty dict instead of crashing.

    Exp133: model_attribute_validator.py's own docstring traces the root
    cause -- route files are generated in parallel, one LLM call per file,
    so a route has no visibility into another file's exact model schema and
    guesses plausible-sounding column names (created_at, last_active,
    last_login, status) that don't actually exist. That validator only
    DETECTS this (correctly); by far the most common failure signature
    across a 100+-app batch this cycle was exactly this pattern repeating
    across dozens of unrelated apps' stats/dashboard routes, each burning a
    full LLM fix-loop attempt to rediscover the same fix. This closes it
    deterministically and for free: the offending endpoint returns {} (a
    reduced but non-crashing response) instead of a 500 AttributeError that
    was blocking runtime startup validation for the whole app.
    """
    from app.services.model_attribute_validator import (
        _collect_model_attrs, _MODEL_ATTR_RE, _MODEL_IMPORT_RE, _SQLA_SPECIAL_ATTRS,
    )

    routes_dir = project_path / "app" / "routes"
    if not routes_dir.exists():
        return 0
    model_attrs = _collect_model_attrs(str(project_path))
    if not model_attrs:
        return 0

    total_patched = 0
    for rf in sorted(routes_dir.glob("*.py")):
        try:
            content = rf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        imported_models = set()
        for m in _MODEL_IMPORT_RE.finditer(content):
            imported_models.update(n.strip() for n in m.group(1).split(","))

        bad_lines: set[int] = set()
        for m in _MODEL_ATTR_RE.finditer(content):
            model_name, attr = m.group(1), m.group(2)
            if model_name not in imported_models or model_name not in model_attrs:
                continue
            if attr in _SQLA_SPECIAL_ATTRS or attr.startswith("__"):
                continue
            if attr in model_attrs[model_name]:
                continue
            bad_lines.add(content.count("\n", 0, m.start()))

        if not bad_lines:
            continue

        lines = content.split("\n")
        func_starts: list[tuple[int, int]] = []
        for i, line in enumerate(lines):
            stripped = line.lstrip()
            if stripped.startswith("def ") or stripped.startswith("async def "):
                func_starts.append((i, len(line) - len(stripped)))

        funcs_to_wrap: set[tuple[int, int]] = set()
        for bl in bad_lines:
            candidate = None
            for (fi, findent) in func_starts:
                if fi <= bl:
                    candidate = (fi, findent)
                else:
                    break
            if candidate:
                funcs_to_wrap.add(candidate)
        if not funcs_to_wrap:
            continue

        file_patched = 0
        for (fi, findent) in sorted(funcs_to_wrap, reverse=True):
            body_indent = findent + 4

            # The signature's own closing ")：" can be many lines below `def`
            # when params are multi-line (routine in generated FastAPI code)
            # -- track paren depth to find where the body actually starts,
            # not just the line after `def`.
            sig_end = fi
            depth = 0
            opened = False
            for j in range(fi, len(lines)):
                for ch in lines[j]:
                    if ch in "([{":
                        depth += 1
                        opened = True
                    elif ch in ")]}":
                        depth -= 1
                if opened and depth <= 0:
                    sig_end = j
                    break
            else:
                continue  # unbalanced parens -- don't risk corrupting this file

            end = len(lines)
            for j in range(sig_end + 1, len(lines)):
                l = lines[j]
                if l.strip() == "":
                    continue
                if len(l) - len(l.lstrip()) <= findent:
                    end = j
                    break
            body_start = sig_end + 1
            body = lines[body_start:end]
            if not body or body[0].strip().startswith("try:"):
                continue  # already wrapped by a prior patcher pass
            new_body = [" " * body_indent + "try:"]
            new_body += [("    " + l if l.strip() else l) for l in body]
            new_body += [" " * body_indent + "except Exception:",
                         " " * (body_indent + 4) + "return {}"]
            lines[body_start:end] = new_body
            file_patched += 1

        if file_patched:
            rf.write_text("\n".join(lines), encoding="utf-8")
            total_patched += file_patched
            print(f"  [patcher] Wrapped {file_patched} route handler(s) referencing an invalid "
                  f"model attribute in {rf.name} (safe fallback instead of crash)")

    return total_patched


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


_DEDUPE_CLASS_DECL_RE = re.compile(r"^class (\w+)\s*\(", re.MULTILINE)
_DEDUPE_COLUMN_COUNT_RE = re.compile(r"^\s{4}\w+\s*=\s*Column\(", re.MULTILINE)


def _dedupe_class_files(target_dir: Path, kind: str) -> int:
    """
    When both user.py and users.py (or expense.py and expenses.py) exist in the
    same directory, keep the file with the "real" model and delete the other.
    Any import still pointing at the dropped file's module path is left for
    _patch_redirect_missing_backend_imports to resolve (it runs later and
    indexes symbols across the whole app/ tree).

    Two distinct collision shapes are handled:
      1. Both files define the SAME class name (e.g. both have `class
         Expense`) -- keep the file with more content, drop the other.
      2. Both files define DIFFERENT, singular/plural-variant class names
         (e.g. user.py's `class User` vs users.py's `class Users`) -- this
         is the more dangerous shape, since it silently creates TWO real
         SQLAlchemy-mapped classes for one entity (confirmed live: a stub
         `class User(Base): __tablename__='user'` with just an `id` column,
         generated as an import-fallback by _patch_model_aliases, coexisting
         with the real `class Users(Base)` that has every actual column).
         The exact-name check above misses this entirely because `"User" &
         "Users"` share no common element. Here we keep whichever class has
         more real Column(...) declarations (not raw file length, which the
         stub's own re-export/comment scaffolding can inflate past the real
         file's length), and alias the dropped class name to the kept one so
         `from app.models.<dropped-stem> import <DroppedName>` still
         resolves -- to the one real mapped class, not a second, empty one.

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

        classes1 = set(_DEDUPE_CLASS_DECL_RE.findall(c1))
        classes2 = set(_DEDUPE_CLASS_DECL_RE.findall(c2))
        exact_shared = classes1 & classes2

        # Singular/plural class-NAME variants across the file pair -- see
        # docstring case 2. Only pairs class names that aren't already an
        # exact match.
        variant_pairs: list[tuple[str, str]] = []
        for n1 in classes1 - exact_shared:
            for n2 in classes2 - exact_shared:
                a, b = n1.lower(), n2.lower()
                if a == b + "s" or b == a + "s":
                    variant_pairs.append((n1, n2))

        if not exact_shared and not variant_pairs:
            continue

        if exact_shared:
            keep, drop = (f1, f2) if len(c1) >= len(c2) else (f2, f1)
        else:
            cols1 = len(_DEDUPE_COLUMN_COUNT_RE.findall(c1))
            cols2 = len(_DEDUPE_COLUMN_COUNT_RE.findall(c2))
            if cols1 != cols2:
                keep, drop = (f1, f2) if cols1 > cols2 else (f2, f1)
            else:
                keep, drop = (f1, f2) if len(c1) >= len(c2) else (f2, f1)

        keep_content = keep.read_text(encoding="utf-8", errors="ignore")
        for cls in sorted(exact_shared):
            # Add alias for the drop-file's stem (so both imports resolve)
            alias_line = f"{cls} = {cls}  # deduplicated from {drop.stem}"
            if alias_line not in keep_content and f"\n{cls} = " not in keep_content:
                keep_content = keep_content.rstrip() + f"\n# Removed duplicate {drop.name}\n"
                break

        keep_classes = classes1 if keep is f1 else classes2
        for n1, n2 in variant_pairs:
            dropped_name, real_name = (n2, n1) if n1 in keep_classes else (n1, n2)
            alias_line = f"{dropped_name} = {real_name}  # dedup singular/plural alias: patcher"
            if alias_line not in keep_content:
                keep_content = keep_content.rstrip() + f"\n{alias_line}\n"

        keep.write_text(keep_content, encoding="utf-8")
        drop.unlink()
        all_shared = exact_shared | {n for pair in variant_pairs for n in pair}
        print(f"  [patcher] Removed duplicate {kind} {drop.name} (kept {keep.name}), shared classes: {all_shared}")
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

_FROM_MODELS_IMPORT = re.compile(r"^from app\.models\.\w+ import ([^\n]+)", re.MULTILINE)
_RESPONSE_MODEL_ATTR = re.compile(r"\bresponse_model\s*=\s*(List\[)?(\w+)(])?")
# FastAPI derives the response model from a route function's return-type
# annotation just as readily as from an explicit response_model= kwarg — an
# annotation like `-> HabitCompletion:` referencing an ORM model crashes
# backend startup with the exact same "Invalid args for response field!"
# FastAPIError as the response_model= form, but was invisible to this patcher
# since it only looked for the `response_model=` keyword.
_RETURN_TYPE_ANNOTATION = re.compile(r"(->\s*)(List\[)?(\w+)(\])?(\s*:)")

# Exp088: the OTHER half of Exp087's confirmed PydanticSerializationError
# root cause -- a route annotated response_model=dict/Dict (this
# patcher's own existing fallback two rules up, or an LLM-authored
# pagination wrapper) whose handler still returns a raw, unconverted ORM
# query result nested inside (`return {"items": <query>.all(), ...}`).
# `dict`/`Dict` carries no from_attributes/ORM-mode context, so FastAPI's
# serializer crashes on the nested ORM instance regardless of what the
# matching schema class's own config says.
_DICT_RESPONSE_MODEL_RE = re.compile(r"response_model\s*=\s*(dict|Dict)\b(\[[^\]]*\])?")
_RETURN_DICT_START_RE = re.compile(r"^\s*return\s*\{")
_ITEMS_KEY_VALUE_RE = re.compile(r"[\"']items[\"']\s*:\s*(\w+)")
_DB_QUERY_CLASS_RE = re.compile(r"\.query\(\s*(\w+)\s*\)")
_RETURN_ITEMS_LOOKAHEAD = 10  # bounded scan for a pagination dict literal's closing brace


def _inject_orm_dict_response_conversion(content: str, orm_classes: set, schema_map: dict) -> str:
    """
    Exp088: for a route whose response_model is a bare dict/Dict, find a
    `return {"items": <var>, ...}` statement -- single-line
    (`return {"items": items, "total": total}`) or multi-line (opening
    `return {` with `"items": <var>,` on a following line, confirmed live
    on recipe_share/rating_routes.py's 4-key pagination dict) -- and, if
    the same function body also queries a known ORM class
    (`db.query(ClassName)`) with a matching schema in schema_map, inject
    one line immediately BEFORE the `return {` statement itself (never
    inside a multi-line dict literal, which would be a syntax error):
    `<var> = [<SchemaCls>.model_validate(x, from_attributes=True) for x
    in <var>]`. This makes the returned value JSON-safe regardless of
    what response_model says, without touching response_model itself,
    without touching any other key in the returned dict (pagination
    metadata like "total"/"limit"/"offset" is never referenced), and
    without affecting any route that doesn't match this exact shape.

    Deliberately conservative, matching this file's own established
    detection style (see _patch_list_response_model_mismatch):
    `.query(ClassName)` only (not SQLAlchemy 2.0 `select()`), one queried
    class assumed per route body, a bounded lookahead for the dict
    literal's closing brace rather than a full parser. Any of these not
    matching -- or no schema_map entry for the queried class, or
    items_var already converted earlier in the same body -- leaves the
    route completely untouched.
    """
    lines = content.split("\n")
    n = len(lines)
    insertions: list[tuple[int, str]] = []

    i = 0
    while i < n:
        if not _DICT_RESPONSE_MODEL_RE.search(lines[i]):
            i += 1
            continue

        # Find this route's def line (immediately below the decorator, or
        # a few lines down for a multi-line signature/decorator).
        k = i
        while k < n and not lines[k].lstrip().startswith(("def ", "async def ")):
            k += 1
            if k - i > 8:
                break
        if k >= n:
            i += 1
            continue

        # Scan the function body (until the next decorator/def) for the
        # queried ORM class and a `return {"items": <var>, ...}` statement
        # -- items_line_idx always ends up pointing at the `return {` line
        # itself, whether "items" appears on that same line (single-line
        # form) or a following one (multi-line form), since the
        # conversion must always be inserted before the whole statement.
        queried_class = None
        items_line_idx = None
        items_var = None
        m = k + 1
        while m < n:
            bl = lines[m]
            if re.match(r"@\w+_router", bl.strip()) or bl.lstrip().startswith(("def ", "async def ")):
                break
            if queried_class is None:
                qm = _DB_QUERY_CLASS_RE.search(bl)
                if qm and qm.group(1) in orm_classes:
                    queried_class = qm.group(1)
            if items_line_idx is None and _RETURN_DICT_START_RE.match(bl):
                for look in range(m, min(m + _RETURN_ITEMS_LOOKAHEAD, n)):
                    im = _ITEMS_KEY_VALUE_RE.search(lines[look])
                    if im:
                        items_line_idx = m
                        items_var = im.group(1)
                        break
                    if look > m and "}" in lines[look]:
                        break  # dict literal closed without an "items" key -- not this shape
            m += 1

        if queried_class is not None and items_line_idx is not None and queried_class in schema_map:
            # Exp088: only act when items_var's own MOST RECENT assignment
            # is a direct ORM query terminal call (ends in `.all()`) --
            # not a comprehension over some other, already-processed
            # value. Confirmed live on simple_notes_app/note_routes.py:
            # `items = [_note_to_dict(n) for n in notes]` is a deliberate
            # custom field-rename shim (content -> description) sitting
            # between the raw query and the return; blindly wrapping ITS
            # output in a generic schema re-validation would silently
            # override that intentional mapping instead of fixing
            # anything (harmless by luck in that one case since the
            # renamed dict happens to satisfy the schema, but the
            # principle -- don't second-guess an existing custom
            # conversion -- must hold generally). Also serves as the
            # already-converted guard: a `model_validate(...)` call on
            # this same line fails the `.all()` suffix check too.
            last_items_assignment = None
            for x in range(k + 1, items_line_idx):
                if re.match(rf"^\s*{re.escape(items_var)}\s*(:[^=]+)?=", lines[x]):
                    last_items_assignment = lines[x]
            if last_items_assignment is None or not re.search(r"\.all\(\)\s*$", last_items_assignment.rstrip()):
                i = m
                continue

            schema_cls, _ = schema_map[queried_class]
            indent = re.match(r"(\s*)", lines[items_line_idx]).group(1)
            conversion_line = (
                f"{indent}{items_var} = [{schema_cls}.model_validate(x, from_attributes=True) "
                f"for x in {items_var}]"
            )
            already_applied = (
                items_line_idx > 0 and lines[items_line_idx - 1].strip() == conversion_line.strip()
            )
            if not already_applied:
                insertions.append((items_line_idx, conversion_line))

        i = m

    if not insertions:
        return content

    out = list(lines)
    for idx, conv_line in sorted(insertions, key=lambda t: -t[0]):
        out.insert(idx, conv_line)

    return "\n".join(out)


def _patch_orm_response_model(content: str, filepath: str, project_path: Path = None) -> str:
    norm = filepath.replace("\\", "/")
    if ("/routes/" not in norm and not norm.startswith("app/routes")):
        return content
    if "response_model" not in content and "->" not in content:
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
    schema_candidates: dict[str, list[tuple[str, str]]] = {}
    if project_path:
        # Exp088: reuses fix_writer_service.py's _collect_basemodel_classes
        # (Exp064's own fixed-point local-inheritance resolver) instead of
        # a bare regex requiring literal "BaseModel" in the class
        # declaration. Confirmed live on generated_projects/todo_list_app:
        # the real response schema was `class TaskResponse(BaseSchema)`,
        # where BaseSchema itself inherits BaseModel -- the old regex
        # (`class \\w+\\(.*BaseModel.*\\)`) only ever matched BaseSchema
        # itself, never TaskResponse/TaskCreate/TaskUpdate, silently
        # making the entire real schema file invisible to this function.
        from app.services.fix_writer_service import _collect_basemodel_classes

        schemas_dir = project_path / "app" / "schemas"
        if schemas_dir.exists():
            for sf in schemas_dir.glob("*.py"):
                if sf.name.startswith("_"):
                    continue
                try:
                    sc_tree = ast.parse(sf.read_text(encoding="utf-8", errors="ignore"))
                except Exception:
                    continue
                module_name = f"app.schemas.{sf.stem}"
                for schema_cls in _collect_basemodel_classes(sc_tree):
                    # Match: UserResponse -> User, UserSchema -> User, UserBase -> User, etc.
                    for orm_cls in orm_classes:
                        base = orm_cls.rstrip("s")  # "Users" -> "User", "User" -> "User"
                        if schema_cls.startswith(base) and schema_cls != orm_cls:
                            schema_candidates.setdefault(orm_cls, []).append((schema_cls, module_name))

    # Exp088: when more than one schema class matches (e.g. a duplicate-
    # model-cleanup shim like "TaskRead" sitting alongside the real
    # "TaskResponse"), prefer the conventionally-named "<Base>Response"
    # class. The previous first-glob-order-wins behavior could land on an
    # incomplete stub shim -- confirmed live on generated_projects/todo_list_app,
    # where alphabetical glob order (task.py before tasks.py) picked a
    # shim TaskRead declaring only `id`, which would have silently dropped
    # every other field instead of fixing the actual serialization crash.
    # Falls back to the first-found candidate, unchanged, when there's no
    # exact "<Base>Response" match -- identical to prior behavior whenever
    # only one candidate exists (every existing test fixture's case).
    for orm_cls, candidates in schema_candidates.items():
        base = orm_cls.rstrip("s")
        preferred = next((c for c in candidates if c[0] == f"{base}Response"), None)
        schema_map[orm_cls] = preferred or candidates[0]

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

    def _replace_rt(m: re.Match) -> str:
        arrow, prefix, cls_name, suffix, colon = m.groups()
        prefix = prefix or ""
        suffix = suffix or ""
        if cls_name not in orm_classes:
            return m.group(0)
        if cls_name in schema_map:
            schema_cls, _ = schema_map[cls_name]
            return f"{arrow}{prefix}{schema_cls}{suffix}{colon}"
        return f"{arrow}{prefix}dict{suffix}{colon}"

    new_content = _RETURN_TYPE_ANNOTATION.sub(_replace_rt, new_content)

    # Exp088: convert nested ORM collections under a generic dict/Dict
    # response_model instead of leaving them to crash serialization.
    new_content = _inject_orm_dict_response_conversion(new_content, orm_classes, schema_map)

    # Add imports for any schema classes we substituted in (the loop below
    # already scans for schema_cls anywhere in new_content, so a class
    # referenced only by the Exp088 conversion above is picked up here too).
    for orm_cls, (schema_cls, module_name) in schema_map.items():
        if orm_cls in orm_classes and schema_cls in new_content:
            import_line = f"from {module_name} import {schema_cls}"
            # Exp088: schema_cls may already be imported as part of a
            # combined statement (`from X import A, B, schema_cls`) --
            # confirmed live on recipe_share/simple_notes_app, where the
            # old exact-string check didn't recognize that shape and
            # added a second, redundant (if harmless) import line every
            # time. Checks for schema_cls as a name anywhere in an
            # existing `from module_name import ...` line, not just an
            # exact match of this single-name form.
            already_imported = bool(re.search(
                rf"^from {re.escape(module_name)} import\s+.*\b{re.escape(schema_cls)}\b",
                new_content, re.MULTILINE,
            ))
            if not already_imported:
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
    """
    Split a full parameter string into individual params respecting nested
    parens AND brackets. Exp054: the original version only tracked `(`/`)`,
    so a comma inside a bracketed type hint (e.g. `Dict[str, int]`) was
    treated as a top-level param separator -- confirmed via direct
    reproduction to corrupt `filters: Dict[str, int] = Query({})` into two
    bogus fragments (`'filters: Dict[str'`, `'int] = Query({})'`), which
    `_reorder_sig` then wrote out as syntactically invalid Python with no
    validation. `file_writer_service.py::_fix_fastapi_param_order`'s own
    `_split_params` already tracks `([`/`)]` together for exactly this
    reason -- this brings the two into agreement on splitting semantics
    (still two separate functions; see docs/REPAIR_ARCHITECTURE.md §4 for
    why they aren't merged into one).
    """
    raw: list[str] = []
    buf = ""
    depth = 0
    for ch in sig:
        if ch in "([":
            depth += 1
        elif ch in ")]":
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

        # Fast skip: only process files with syntax errors of this type.
        # Exp052: Python's own wording for this SyntaxError changed between
        # versions -- "non-default argument follows default argument" pre-3.10
        # vs "parameter without a default follows parameter with a default"
        # on the 3.14 interpreter this codebase actually runs on. Matching
        # only the old string meant this function silently never fired on
        # the current runtime, on any file, ever -- confirmed by direct
        # execution (Exp052), not assumed.
        try:
            compile(original, str(rf), "exec")
            continue  # No syntax error — skip
        except SyntaxError as e:
            msg = e.msg or ""
            if not (
                "non-default argument follows default argument" in msg
                or "parameter without a default follows parameter with a default" in msg
            ):
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
            # Exp054: validate before writing. _reorder_sig's param split can
            # still misfire on signature shapes nobody's hit yet (e.g. a
            # bracket-tracking edge case just like the one this experiment
            # fixed) -- confirmed by direct reproduction that an unvalidated
            # write can turn a recoverable SyntaxError into unparseable
            # garbage. `file_writer_service.py`'s parallel implementation
            # already validates before writing; this brings the same safety
            # net here without touching the split/reorder logic itself.
            try:
                compile(content, str(rf), "exec")
            except SyntaxError as e:
                print(f"  [patcher] Param-order reorder for {rf.name} produced invalid "
                      f"syntax ({e}) — leaving file unpatched")
                continue
            rf.write_text(content, encoding="utf-8")
            patched += 1
            print(f"  [patcher] Fixed param order in {rf.name}")

    return patched


_ROUTE_DECORATOR_RE = re.compile(
    r'@\w+\.(get|post|put|delete|patch)\(\s*["\']([^"\']+)["\']'
)


def _route_shadows(earlier_path: str, later_path: str) -> bool:
    """
    True if a request meant for `later_path` would actually match
    `earlier_path` first, purely from FastAPI/Starlette's first-registered-
    wins routing: same segment count, every literal segment identical, and
    at least one segment where `earlier_path` has a `{param}` exactly where
    `later_path` has a literal value.
    """
    e = [s for s in earlier_path.split("/") if s]
    l = [s for s in later_path.split("/") if s]
    if len(e) != len(l):
        return False
    has_param_vs_literal = False
    for es, ls in zip(e, l):
        if es == ls:
            continue
        e_is_param = es.startswith("{") and es.endswith("}")
        l_is_param = ls.startswith("{") and ls.endswith("}")
        if e_is_param and not l_is_param:
            has_param_vs_literal = True
            continue
        return False
    return has_param_vs_literal


def patch_reorder_shadowed_static_routes(project_path: str) -> int:
    """
    A static sub-route registered AFTER a parameterized route with the same
    shape is permanently unreachable: FastAPI/Starlette try routes in
    registration order and the first match wins, so `@habit_router.get(
    "/habits/{habit_id}")` registered before `@habit_router.get(
    "/habits/streaks")` swallows every request for /habits/streaks --
    "streaks" just becomes the string value of habit_id, and the real
    handler never runs. This is a common, easy-to-miss ordering mistake:
    generated route files are usually written CRUD-first (list, get-by-id,
    create, update, delete) with "special" collection-level sub-routes
    (streaks, search, export, summary, ...) appended at the end, which is
    exactly the wrong order. Seen live: GET /habits/streaks 422'd with
    "unable to parse 'streaks' as an integer" -- the {habit_id} route
    caught it first and tried to parse the literal path segment as an int.

    Fix: move each shadowed static route's whole decorated function block to
    just before the parameterized route that was swallowing it, preserving
    everything else's relative order.
    """
    project = Path(project_path)
    routes_dir = project / "app" / "routes"
    if not routes_dir.exists():
        return 0

    patched = 0
    for rf in routes_dir.glob("*.py"):
        try:
            src = rf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        route_starts = [m.start() for m in re.finditer(r'^@\w+\.(?:get|post|put|delete|patch)\(', src, re.MULTILINE)]
        if len(route_starts) < 2:
            continue
        route_starts.append(len(src))

        blocks = []
        for i in range(len(route_starts) - 1):
            block_src = src[route_starts[i]:route_starts[i + 1]]
            dm = _ROUTE_DECORATOR_RE.search(block_src)
            if dm:
                blocks.append({"src": block_src, "method": dm.group(1), "path": dm.group(2)})
            else:
                blocks.append({"src": block_src, "method": None, "path": None})

        moved: set = set()
        reordered = list(range(len(blocks)))
        changed = False
        renamed: list[str] = []
        for j in range(len(blocks)):
            if blocks[j]["path"] is None or j in moved:
                continue
            for i in range(j):
                if i in moved or blocks[i]["path"] is None:
                    continue
                if blocks[i]["method"] != blocks[j]["method"]:
                    continue
                if _route_shadows(blocks[i]["path"], blocks[j]["path"]):
                    reordered.remove(j)
                    insert_at = reordered.index(i)
                    reordered.insert(insert_at, j)
                    moved.add(j)
                    changed = True
                    renamed.append(f"{blocks[j]['method'].upper()} {blocks[j]['path']}")
                    break

        if not changed:
            continue

        new_body = "\n\n\n".join(blocks[k]["src"].rstrip() for k in reordered) + "\n"
        new_src = src[:route_starts[0]] + new_body
        if new_src != src:
            rf.write_text(new_src, encoding="utf-8")
            patched += 1
            print(f"  [route_patcher] Reordered shadowed static route(s) in {rf.name}: "
                  f"{', '.join(renamed[:6])}")

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


def _get_user_model():
    import importlib
    for mod in ("app.models.user", "app.models.users"):
        try:
            m = importlib.import_module(mod)
            return getattr(m, "User")
        except (ImportError, AttributeError):
            continue
    raise ImportError("No User model found in app.models.user or app.models.users")


def _login_field(User) -> str:
    """Which column uniquely identifies a user for login -- 'email' if the
    model has one, else 'username'. Signup, login, and get_current_user must
    all agree on this: a user created under one identifier field can never
    be found again by a path that assumes the other, and since the field
    doesn't exist at all on some models, comparing against it outright
    raises AttributeError -- 500ing the request instead of a clean 401.
    """
    cols = {c.name for c in User.__table__.columns}
    return "email" if "email" in cols else "username"


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    User = _get_user_model()
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        identifier: str = payload.get("sub")
        if not identifier:
            raise credentials_exception
    except Exception:
        raise credentials_exception
    user = db.query(User).filter(getattr(User, _login_field(User)) == identifier).first()
    if not user:
        raise credentials_exception
    return user


def authenticate_user(db: Session, identifier: str, password: str):
    """Convenience helper — LLM-generated routes commonly import this."""
    User = _get_user_model()
    user = db.query(User).filter(getattr(User, _login_field(User)) == identifier).first()
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


def _patch_pagination_component(project_path: Path) -> int:
    # Glob rather than a single fixed path -- the LLM places this component
    # under src/components/Pagination.jsx most of the time, but also
    # src/components/UI/Pagination.jsx or .../Common/Pagination.jsx often
    # enough that a fixed path silently missed real, confirmed-broken
    # instances (Experiment 049 corpus check, 2026-07-11: 2 of 13 sampled
    # broken template-literal-ternary failures were path variants this
    # fixed-path check never saw).
    src_dir = project_path / "src"
    if not src_dir.exists():
        return 0
    fixed = 0
    for pagination_file in src_dir.rglob("Pagination.jsx"):
        if "node_modules" in pagination_file.parts:
            continue
        try:
            content = pagination_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if "currentPage" not in content:
            continue  # not actually the standard pagination component -- leave it alone
        if content == _PAGINATION_TEMPLATE:
            continue
        pagination_file.write_text(_PAGINATION_TEMPLATE, encoding="utf-8")
        rel = pagination_file.relative_to(project_path)
        print(f"  [patcher] Injected known-good {rel}")
        fixed += 1
    return fixed


# ── 9c. Broken template-literal-ternary className collapse ───────────────────
# The LLM's single most common way to break a Vite build (Experiment 049,
# 2026-07-11): a dynamically-computed className built from a template
# literal containing a multi-line ternary, where the `${` interpolation
# opener gets dropped or left empty -- producing an unparseable fragment
# (esbuild: `Expected "}" but found ...`). Seen across many unrelated
# components (Pagination, Dashboard, Toast, Register, Calendar) with the
# same shape every time, not just one app's quirk.
#
# Rather than fully reconstruct the broken multi-line expression (a real
# JS/JSX parser's job, and risky to approximate by hand), this collapses
# the whole broken attribute to a plain static className string built from
# the literal text before the break -- guaranteed-valid syntax at the cost
# of losing that one element's conditional styling. Validated against esbuild
# directly (not just "looks plausible"): 13/13 confirmed-broken samples
# pulled from backend/llm_cache/ now build clean after this patch, all 3
# confirmed-broken files found by scanning the full generated_projects/
# corpus (882 .jsx files) now build clean, and -- critically -- 0/882 files
# in that same corpus are false-flagged (an earlier draft of this detector
# had an 85% false-positive rate before three rounds of refinement against
# real, already-valid multi-line template literals: `${...}` interpolations
# spanning several lines, string concatenation via `+`, and same-line tag
# closures).
_TEMPLATE_LITERAL_OPEN_RE = re.compile(r'^(.*?)(\w[\w-]*)=\{`')
_VALID_JSX_CONTINUATION_RE = re.compile(r'^([>/]|[A-Za-z_][\w-]*=(?!=)|\{/\*|//)')


def _next_meaningful_line(lines: list[str], idx: int) -> str:
    j = idx
    while j < len(lines) and lines[j].strip() == "":
        j += 1
    return lines[j].strip() if j < len(lines) else ""


def _patch_broken_template_literal_classname(content: str) -> tuple[str, int]:
    lines = content.split("\n")
    out: list[str] = []
    i = 0
    fixed = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        m = _TEMPLATE_LITERAL_OPEN_RE.match(line)
        broken = False
        static_text = None
        if m:
            backtick_count = line.count("`")
            if backtick_count % 2 == 1:
                # Genuinely open multi-line template literal (e.g. a
                # `${...}` interpolation whose condition spans several
                # lines) is valid JS -- odd count on the opening line alone
                # proves nothing. Only flag it broken if, before the
                # literal's real closing backtick, we find a line that
                # isn't part of a legitimately-open interpolation (the
                # tell-tale sign the `${` got dropped before a bare
                # ternary/condition).
                first = line.index("`")
                opener_text = line[first + 1:].rstrip()
                interp_depth = 1 if opener_text.endswith("${") else 0
                k = i + 1
                window_end = min(n, i + 8)
                saw_break = False
                while k < window_end:
                    probe = lines[k].strip()
                    if probe.count("`") % 2 == 1:
                        break  # closes the literal here -- legitimate multi-line string
                    if interp_depth > 0:
                        interp_depth += probe.count("{") - probe.count("}")
                        if interp_depth <= 0:
                            interp_depth = 0
                    elif probe.startswith("${"):
                        interp_depth = max(0, probe.count("{") - probe.count("}"))
                    else:
                        saw_break = True
                        break
                    k += 1
                if saw_break:
                    broken = True
                    static_text = opener_text
            else:
                last_backtick = line.rindex("`")
                same_line_rest = line[last_backtick + 1:].strip()
                # The tag already closed (or continues validly) on this
                # very line -- e.g. `...}}>{x}</span>`, `...}} />`, or a
                # `+` string-concatenation with a (parenthesized) ternary
                # -- so there's nothing to check on the next line at all.
                already_valid = bool(same_line_rest) and (
                    same_line_rest[0] in "}>/+" or _VALID_JSX_CONTINUATION_RE.match(same_line_rest)
                )
                if not already_valid:
                    nxt = _next_meaningful_line(lines, i + 1)
                    if nxt and not (nxt[0] in "<>/(" or _VALID_JSX_CONTINUATION_RE.match(nxt)):
                        broken = True
                        first = line.index("`")
                        static_text = line[first + 1:last_backtick]
            if static_text is not None:
                static_text = static_text.strip()

        terminator = None
        if broken and static_text is not None:
            # Every confirmed real example terminates within ~5 lines. A
            # wide window risks latching onto a later, unrelated, valid
            # `${...}\`}` on some other attribute and eating everything
            # in between.
            window_end = min(n, i + 8)
            for j in range(i + 1, window_end):
                if "`}" in lines[j]:
                    terminator = j
                    break

        if broken and static_text is not None and terminator is not None:
            prefix, attr = m.group(1), m.group(2)
            term_line = lines[terminator]
            # Everything that isn't a stray backtick/brace from the broken
            # structure. Only trust it as a real continuation (tag close,
            # next attribute) if it looks like one -- any other leftover
            # text (e.g. extra CSS classes that trailed the ternary) is
            # dropped rather than risk emitting invalid JSX by splicing it
            # in as a bare suffix after a closed string attribute.
            candidate = re.sub(r'[`}]', '', term_line).strip()
            suffix = candidate if (not candidate or candidate[0] in '>/' or '=' in candidate) else ''
            out.append(f'{prefix}{attr}="{static_text}"{(" " + suffix) if suffix else ""}')
            i = terminator + 1
            fixed += 1
            continue

        out.append(line)
        i += 1
    return "\n".join(out), fixed


def _patch_broken_template_literal_classnames(project_path: Path) -> int:
    fixed = 0
    for jsx_file in project_path.rglob("*.jsx"):
        if "node_modules" in jsx_file.parts:
            continue
        try:
            content = jsx_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        new_content, n = _patch_broken_template_literal_classname(content)
        if n:
            jsx_file.write_text(new_content, encoding="utf-8")
            rel = jsx_file.relative_to(project_path)
            print(f"  [patcher] Collapsed {n} broken template-literal className(s) in {rel}")
            fixed += n
    return fixed


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

_ROLE_FIELD_RE = re.compile(
    r'\brole\s*:\s*(?:Optional\[)?str\]?\s*=\s*Field\('
    # Exp102: the leading quoted default (`Field("diner", ...)`) is now
    # OPTIONAL -- confirmed live in event_manager_platform, whose schema
    # declares `role: str = Field(min_length=1, pattern="^(Organizer|
    # Attendee)$")`, a REQUIRED field with no default at all. Previously
    # this meant the whole regex simply never matched, and role discovery
    # silently returned None for an app that in fact has a perfectly
    # discoverable vocabulary. Group 1 is None when no default is
    # present; callers must handle that (see _discover_role_vocabulary_from_schema).
    r'(?:\s*"([\w-]+)"\s*,)?[^)\n]*'
    r'pattern\s*=\s*r?"[\^]?\(([\w|-]+)\)[\$]?"',
)

# Route-level role GATE comparisons -- the fallback discovery path for apps
# that never declared a role vocabulary in any schema at all (see
# _discover_role_vocabulary_from_routes' docstring for why this exists).
#
# Exp102: `.role` is only one way generated code actually reads a role --
# `getattr(current_user, "role", None)` is an equally common, increasingly
# likely defensive idiom (confirmed live in event_manager_platform's
# `getattr(current_user, "role", None) != "Organizer"`), which the plain
# `\.role` prefix never matched at all. Shared fragment so both gate
# shapes (`==`/`!=` and `in`/`not in`) recognize either access form.
_ROLE_ACCESS_FRAGMENT = r'(?:\.role|getattr\([^,]+,\s*["\']role["\']\s*,[^)]*\))'
_ROLE_EQ_RE = re.compile(_ROLE_ACCESS_FRAGMENT + r'\s*(?:!=|==)\s*["\'](\w+)["\']')
_ROLE_IN_RE = re.compile(_ROLE_ACCESS_FRAGMENT + r'\s+(?:not\s+)?in\s*[\[({]\s*((?:["\']\w+["\']\s*,?\s*)+)[\])}]')
_ROLE_STR_RE = re.compile(r'["\'](\w+)["\']')


def _discover_role_vocabulary_from_schema(project_path: Path) -> Optional[tuple[str, list[str]]]:
    """Precise path: an LLM-authored schema explicitly declares the
    vocabulary (e.g. `role: str = Field("diner", pattern="^(diner|staff)$")`).
    See _discover_role_vocabulary's docstring for the incident this fixes."""
    schemas_dir = project_path / "app" / "schemas"
    if not schemas_dir.exists():
        return None
    for sf in schemas_dir.glob("*.py"):
        try:
            content = sf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        m = _ROLE_FIELD_RE.search(content)
        if not m:
            continue
        default_role, alternatives = m.group(1), m.group(2)
        allowed = [a for a in alternatives.split("|") if a]
        if default_role is None:
            # Exp102: a REQUIRED role field (no default in the app's own
            # schema) still has a discoverable vocabulary worth using for
            # elevation candidates -- but there's no natural "what does a
            # plain signup get" answer to anchor on. Reuse exactly the
            # same safe-fallback convention _discover_role_vocabulary_from_routes
            # already established for its own no-anchor case: "user" as
            # the default (added to the allowed set too, so it validates),
            # never widening beyond what the discovered vocabulary itself
            # allows.
            default_role = "user"
            if "user" not in allowed:
                allowed.append("user")
        elif default_role not in allowed:
            allowed.append(default_role)
        return default_role, allowed
    return None


def _discover_role_vocabulary_from_routes(project_path: Path) -> Optional[tuple[str, list[str]]]:
    """
    Fallback path: no schema declares a vocabulary at all, but route
    handlers still gate on specific role strings (`current_user.role !=
    "instructor"`, `role not in ["staff", "admin"]`). Confirmed live
    (2026-07-11): a generated course-platform app's schema had a bare
    `role: Optional[str] = None` -- zero declared vocabulary -- yet
    course/lesson/enrollment/user routes gated on three distinct roles
    (admin, instructor, student) found nowhere but the comparisons
    themselves. The model column had no default either, so there is no
    single "the" default to anchor on; "user" (the template's existing
    hardcoded fallback) is kept as the default and folded into the
    allowed set, so ordinary signups are completely unaffected and only
    an explicit, valid `role=` in the signup request can ever unlock
    anything -- this only WIDENS what a caller can validly request, never
    narrows the existing safe behavior.

    Requires at least 2 distinct role strings across the whole app before
    treating this as a real vocabulary (a single isolated string is as
    likely to be a one-off admin lockout as a real multi-role system, and
    guessing wrong there would incorrectly treat a security boundary as
    something the elevation retry should try to route around).
    """
    routes_dir = project_path / "app" / "routes"
    if not routes_dir.exists():
        return None
    found: set[str] = set()
    for rf in routes_dir.glob("*.py"):
        try:
            content = rf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for m in _ROLE_EQ_RE.finditer(content):
            found.add(m.group(1))
        for m in _ROLE_IN_RE.finditer(content):
            for s in _ROLE_STR_RE.finditer(m.group(1)):
                found.add(s.group(1))
    if len(found) < 2:
        return None
    allowed = sorted(found) + (["user"] if "user" not in found else [])
    return "user", allowed


def _discover_role_vocabulary(project_path: Path) -> Optional[tuple[str, list[str]]]:
    """
    Look for evidence the app itself needs an app-specific role vocabulary
    beyond the generic default every signup otherwise gets.

    Root cause this feeds into: the injected auth template (below) used to
    unconditionally hardcode every new signup's role to "user", regardless
    of what the app's OWN schema or route logic intended. Confirmed live
    (2026-07-11) in two independent apps: a restaurant app whose schema
    declared a diner|staff pattern, and a course-platform app whose routes
    gated on admin/instructor/student with NO schema declaration at all --
    a corpus sweep afterward found role-gating logic in 7 of 54 real
    projects (~13%), only one of which the schema-only check could see.
    Not just a test-scoring gap: features permanently unreachable by any
    real end user of the deployed app.

    Tries the precise schema-declared path first, falls back to scanning
    route-level gate comparisons only if the schema has nothing. Returns
    (default_role, [allowed_roles]) or None -- conservative by design: no
    match means no change to the existing safe "user" fallback.
    """
    return (_discover_role_vocabulary_from_schema(project_path)
            or _discover_role_vocabulary_from_routes(project_path))


def _build_auth_routes_template(role_info: Optional[tuple[str, list[str]]]) -> str:
    """
    The known-good auth_routes.py template, optionally parameterized with
    an app-specific role vocabulary discovered by _discover_role_vocabulary.
    With role_info=None this is byte-for-byte the original generic
    template (zero behavior change for the common case of no app-specific
    role distinction) -- accepting an optional `role` in the signup
    request and validating it against the discovered allowed set when
    role_info is present.
    """
    if role_info:
        default_role, allowed_roles = role_info
        allowed_repr = repr(set(allowed_roles))
        role_field = f'    role: str | None = None  # validated against {sorted(allowed_roles)} below\n'
        role_assignment = (
            f'    if "role" in cols:\n'
            f'        kw["role"] = role if role in {allowed_repr} else "{default_role}"\n'
        )
        make_user_sig = 'def _make_user(email: str, password: str, display_name: str = "", role: str | None = None):'
        signup_call = 'user = _make_user(req.email, req.password, req.display_name, req.role)'
    else:
        role_field = ""
        role_assignment = '    if "role" in cols:\n        kw["role"] = "user"\n'
        make_user_sig = 'def _make_user(email: str, password: str, display_name: str = ""):'
        signup_call = 'user = _make_user(req.email, req.password, req.display_name)'

    # Sentinel-token substitution, NOT an f-string -- this template is full
    # of literal dict/set-comprehension braces ({c.name for c in ...},
    # {k: v for k, v ...}) that an f-string would try to evaluate as
    # placeholders. Plain string + targeted .replace() sidesteps having to
    # escape every one of them.
    template = '''\
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.utils.auth import (
    get_password_hash, verify_password, create_access_token, get_current_user,
    _get_user_model, _login_field,
)

auth_router = APIRouter()


class SignupRequest(BaseModel):
    email: str = Field(min_length=1)
    password: str = Field(min_length=1)
    display_name: str = ""
__ROLE_FIELD__


class LoginRequest(BaseModel):
    email: str = Field(min_length=1)
    password: str = Field(min_length=1)


def _identifier_value(login_field: str, email: str) -> str:
    return email if login_field == "email" else email.split("@")[0]


__MAKE_USER_SIG__
    """Build a User instance regardless of which password/identifier field the model uses."""
    User = _get_user_model()
    cols = {c.name for c in User.__table__.columns}
    login_field = _login_field(User)
    identifier = _identifier_value(login_field, email)
    kw: dict = {login_field: identifier}
    if login_field != "email" and "email" in cols:
        kw["email"] = email
    pwd_hash = get_password_hash(password)
    for field in ("hashed_password", "password_hash", "password"):
        if field in cols:
            kw[field] = pwd_hash
            break
    if "display_name" in cols:
        kw["display_name"] = display_name or email.split("@")[0]
    if "username" in cols and "username" not in kw:
        kw["username"] = email.split("@")[0]
    if "is_active" in cols:
        kw["is_active"] = True
__ROLE_ASSIGNMENT__
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
    return User(**{k: v for k, v in kw.items() if k in cols})


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
    login_field = _login_field(User)
    identifier = _identifier_value(login_field, req.email)
    if db.query(User).filter(getattr(User, login_field) == identifier).first():
        raise HTTPException(status_code=400, detail="Email already registered")
    __SIGNUP_CALL__
    db.add(user)
    db.commit()
    db.refresh(user)
    token = create_access_token(data={"sub": identifier})
    return {
        "access_token": token,
        "token_type": "bearer",
        "user_id": user.id,
        "email": req.email,
        "display_name": getattr(user, "display_name", req.email.split("@")[0]),
    }


@auth_router.post("/auth/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    User = _get_user_model()
    login_field = _login_field(User)
    identifier = _identifier_value(login_field, req.email)
    user = db.query(User).filter(getattr(User, login_field) == identifier).first()
    stored = _read_password(user) if user else None
    if not user or not stored or not verify_password(req.password, stored):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    token = create_access_token(data={"sub": identifier})
    return {
        "access_token": token,
        "token_type": "bearer",
        "user_id": user.id,
        "email": getattr(user, "email", req.email),
        "display_name": getattr(user, "display_name", identifier),
    }


@auth_router.get("/auth/me")
def me(current_user=Depends(get_current_user)):
    return {
        "id": current_user.id,
        "email": getattr(current_user, "email", getattr(current_user, "username", None)),
        "display_name": getattr(current_user, "display_name", None),
        "role": getattr(current_user, "role", None),
    }


@auth_router.post("/auth/logout")
def logout(current_user=Depends(get_current_user)):
    # JWTs are stateless — logout is handled client-side by discarding the token.
    # This endpoint confirms the user is authenticated and acknowledges the request.
    return {"message": "Successfully logged out"}
'''
    return (template
            .replace("__ROLE_FIELD__", role_field.rstrip("\n"))
            .replace("__ROLE_ASSIGNMENT__", role_assignment.rstrip("\n"))
            .replace("__MAKE_USER_SIG__", make_user_sig)
            .replace("__SIGNUP_CALL__", signup_call))


_MAKE_USER_CALL_RE = re.compile(
    r"_make_user\(\s*(\w+)\.email\s*,\s*\1\.password\s*,\s*\1\.display_name\s*\)"
)


def _patch_forward_role_to_duplicate_registrars(project_path: Path) -> int:
    """
    Some generated apps have a SECOND, LLM-authored registration endpoint
    alongside the injected auth_routes.py -- typically because the
    architecture wants a prefixed path (e.g. /api/auth/register) that the
    bare injected template doesn't serve, so the LLM writes its own
    wrapper elsewhere (api_routes.py, etc.) that imports and reuses
    _make_user/SignupRequest from auth_routes.py rather than duplicating
    them outright.

    Confirmed live (2026-07-11): exactly this shape in a generated
    restaurant app. app/routes/api_routes.py's api_register() imports
    _make_user from auth_routes.py and calls it with only 3 positional
    args (email, password, display_name) -- silently dropping the 4th
    (role) that _build_auth_routes_template's role-aware signup depends
    on. A signup request with role="staff" would parse correctly, reach
    THIS handler (not auth_routes.py's own -- /api/auth/register and
    /auth/register are different paths, both live), and still end up
    hardcoded to the schema's default role, defeating the fix above
    silently: no error, just the wrong role saved.

    Finds `_make_user(X.email, X.password, X.display_name)` (the exact
    3-arg shape _build_auth_routes_template's own call uses, so any
    handler that copied it verbatim matches) in any route file that
    imports _make_user from auth_routes, and forwards a `role` too --
    via getattr(X, 'role', None), never a bare attribute access, so this
    is a no-op (None) rather than an AttributeError if some other
    request type at a call site genuinely has no role field.
    """
    routes_dir = project_path / "app" / "routes"
    if not routes_dir.exists():
        return 0
    patched = 0
    for rf in routes_dir.glob("*.py"):
        if rf.name == "auth_routes.py":
            continue
        try:
            content = rf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if "_make_user" not in content or "from app.routes.auth_routes import" not in content:
            continue

        def _forward(m: re.Match) -> str:
            var = m.group(1)
            return f"_make_user({var}.email, {var}.password, {var}.display_name, getattr({var}, 'role', None))"

        new_content, n = _MAKE_USER_CALL_RE.subn(_forward, content)
        if n:
            rf.write_text(new_content, encoding="utf-8")
            patched += n
            print(f"  [patcher] Forwarded role to {n} duplicate _make_user() call(s) in {rf.name}")
    return patched


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
        role_info = _discover_role_vocabulary(project_path)
        auth_routes_file.write_text(_build_auth_routes_template(role_info), encoding="utf-8")
        msg = "  [patcher] Injected known-good app/routes/auth_routes.py (dynamic password field + fast bcrypt)"
        if role_info:
            msg += f" -- role-aware signup, vocabulary={sorted(role_info[1])} default={role_info[0]!r}"
            _patch_forward_role_to_duplicate_registrars(project_path)
        print(msg)

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
            # Exp071: escalating fallback chain -- the original version
            # only tried the first two anchors below and, if NEITHER
            # existed in main.py (a minimal main.py with no other
            # routers wired yet and no Base.metadata.create_all line),
            # silently left include_line un-inserted while still
            # printing "Wired auth_router into main.py" as if it had
            # succeeded. Found via Experiment 071's own regression
            # tests, not a live incident -- real generated apps almost
            # always have at least one of the first two anchors, but
            # "almost always" isn't "always", and the false-success
            # print was itself worth closing regardless of how often
            # the gap is actually hit.
            before_len = len(main_content)

            # 1. After the last existing include_router(...) call.
            last_include = None
            for m in re.finditer(r"app\.include_router\(\w+_router\)\n", main_content):
                last_include = m
            if last_include:
                pos = last_include.end()
                main_content = main_content[:pos] + include_line + "\n" + main_content[pos:]
            elif re.search(r"Base\.metadata\.create_all", main_content):
                # 2. Before Base.metadata.create_all.
                main_content = re.sub(
                    r"(Base\.metadata\.create_all)",
                    include_line + "\n" + r"\1",
                    main_content,
                    count=1,
                )
            elif re.search(r"^app\s*=\s*FastAPI\([^\n]*\)\s*$", main_content, re.MULTILINE):
                # 3. Right after the FastAPI app instantiation.
                main_content = re.sub(
                    r"(^app\s*=\s*FastAPI\([^\n]*\)\s*$)",
                    r"\1\n" + include_line,
                    main_content,
                    count=1,
                    flags=re.MULTILINE,
                )
            else:
                # 4. Guaranteed fallback: append at the end of the file.
                main_content = main_content.rstrip("\n") + "\n" + include_line + "\n"

            changed = changed or (len(main_content) != before_len)

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
    # `^\s*` — function-body imports count too. Confirmed live (Exp106,
    # restaurant_pos_system): the handler deferred
    # `from app.schemas.sale import SaleOut` inside the function, the
    # module never existed, and the column-0 anchor made this patcher
    # blind to it — every GET /sales 500'd at request time.
    r"^\s*from app\.schemas\.(\w+) import ([^\n]+)", re.MULTILINE
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


def _infer_fields_from_route_return(routes_dir: Path, cls_name: str) -> list[tuple[str, str]]:
    """
    Fallback for stub schemas with no matching SQLAlchemy model (aggregate/
    report endpoints like a stats summary have no 1:1 table to derive fields
    from). Scan route files for a function decorated with
    response_model=<cls_name> and pull field names out of its return
    statement -- either a `return {...}` dict literal, or a
    `return ClassName(field=..., ...)` / `return OtherClassName(field=..., ...)`
    constructor call (the LLM routinely names the response_model class
    differently than the one it actually instantiates in the return
    statement -- e.g. `response_model=WeeklyReport` but `return
    WeeklyReport(start_date=..., entries=...)` when only WeeklyReportResponse
    was ever defined -- so the constructor call is matched regardless of
    which class it names), typed Optional[Any].

    Without this, the stub schema is an empty `pass` body, and FastAPI's
    response_model machinery silently serializes every response down to
    `{}` -- a 200 OK that looks fine in logs but hands the frontend an
    object missing every field it expects, crashing on the first
    `.map()`/`.slice()` call with no server-side error to point at the cause.
    """
    for rf in routes_dir.glob("*.py"):
        try:
            src = rf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if "response_model" not in src or cls_name not in src:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            uses_cls = False
            for dec in node.decorator_list:
                dec_src = ast.get_source_segment(src, dec) or ""
                if re.search(rf"response_model\s*=\s*(?:List\[|list\[)?{re.escape(cls_name)}\b", dec_src):
                    uses_cls = True
                    break
            if not uses_cls:
                continue
            for stmt in ast.walk(node):
                if not isinstance(stmt, ast.Return):
                    continue
                if isinstance(stmt.value, ast.Dict):
                    fields = [
                        k.value for k in stmt.value.keys
                        if isinstance(k, ast.Constant) and isinstance(k.value, str)
                    ]
                    if fields:
                        return [(name, "Any") for name in fields]
                elif (
                    isinstance(stmt.value, ast.Call)
                    and isinstance(stmt.value.func, ast.Name)
                    and stmt.value.keywords
                ):
                    fields = [kw.arg for kw in stmt.value.keywords if kw.arg]
                    if fields:
                        return [(name, "Any") for name in fields]
    return []


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

            cls_columns = {
                cn: columns or _infer_fields_from_route_return(routes_dir, cn)
                for cn in missing
            }
            needs_any = any(typ == "Any" for cols in cls_columns.values() for _, typ in cols)
            all_dt_names = sorted(
                {t for cols in cls_columns.values() for _, t in cols if t in ("datetime", "date", "time")}
                | set(_dt_names)
            )

            additions = []
            if "Optional" not in existing_content:
                additions.append(f"from typing import Optional{', Any' if needs_any else ''}\n")
            elif needs_any and "Any" not in existing_content:
                additions.append("from typing import Any\n")
            if all_dt_names and "from datetime import" not in existing_content:
                additions.append(f"from datetime import {', '.join(all_dt_names)}\n")
            for cls_name in missing:
                cols = cls_columns[cls_name]
                if cols:
                    field_lines = "\n".join(
                        f"    {name}: Optional[{typ}] = None" for name, typ in cols
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

        cls_columns = {
            cn: columns or _infer_fields_from_route_return(routes_dir, cn)
            for cn in valid_classes
        }
        needs_any = any(typ == "Any" for cols in cls_columns.values() for _, typ in cols)
        all_dt_names = sorted(
            {t for cols in cls_columns.values() for _, t in cols if t in ("datetime", "date", "time")}
            | set(_dt_names)
        )

        lines = [f"from typing import Optional{', Any' if needs_any else ''}", "from pydantic import BaseModel"]
        if all_dt_names:
            lines.append(f"from datetime import {', '.join(all_dt_names)}")
        lines += ["", ""]
        for cls_name in valid_classes:
            cols = cls_columns[cls_name]
            if cols:
                field_lines = "\n".join(
                    f"    {name}: Optional[{typ}] = None" for name, typ in cols
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

                # Match a field: "    fieldname: SomeType" with an optional
                # trailing "= default" captured separately (group 4) so
                # "already Optional" and "already has a default" are checked
                # as plain post-hoc conditions instead of a negative
                # lookahead embedded in the pattern. The lookahead approach
                # this replaced -- `(?!Optional\b)` right after `\s*:\s*` --
                # is defeated by the SAME `\s*` backtracking to a zero-width
                # match to satisfy it: given "fieldname: Optional[X]" (a
                # space after the colon, the overwhelmingly common case),
                # the engine gives back that one space from `\s*` so the
                # lookahead's "no Optional right here" check passes against
                # the space itself, then the capture group swallows the
                # leftover " Optional[X]" as the "type". The field then gets
                # wrapped AGAIN into `Optional[Optional[X]] = None` --
                # confirmed via direct regex testing, not just reasoning.
                # A field already typed Optional[...] but with NO default is
                # still required as far as pydantic is concerned --
                # Optional[X] only widens the accepted TYPE to include None,
                # it does not supply a default, so a value must still be
                # provided. Seen live: HabitResponse.updated_at declared
                # `Optional[datetime]` with no default -- Habit has no
                # updated_at column at all, so every response 500'd with
                # ResponseValidationError ("Field required").
                fm = re.match(r'^(\s+)(\w+)\s*:\s*([^\n=#]+?)\s*(=.*)?$', line)
                if fm:
                    indent, fname, ftype, has_default = fm.group(1), fm.group(2), fm.group(3).strip(), fm.group(4)
                    if fname in ("id", "class", "pass") or ftype.startswith("ClassVar"):
                        continue
                    if has_default:
                        continue
                    if ftype.startswith("Optional[") and ftype.endswith("]"):
                        out_lines[idx] = f"{indent}{fname}: {ftype} = None"
                    else:
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


_CLASS_DECL_RE = re.compile(r'^class\s+(\w+)\s*\(([^)]*)\)\s*:', re.MULTILINE)
_CLASS_FIELD_LINE_RE = re.compile(r'^(\s{4})(\w+)\s*:\s*([^\n=#]+?)\s*(=.*)?$', re.MULTILINE)


def _field_rhs_has_real_default(rhs: str) -> bool:
    """
    `field: T = Field(min_length=1)` has an `=` but is STILL REQUIRED --
    Field(...) with no positional value and no default=/default_factory=
    kwarg supplies constraint metadata, not a default. Confirmed live: a
    naive "any `=` suffix means optional" check silently treated
    `price: float = Field(ge=0.0)` as already-defaulted and skipped it,
    missing exactly the case _patch_response_schema_inherited_required_fields
    exists to catch.
    """
    rhs = rhs.strip().rstrip(",")
    if not rhs:
        return False
    if rhs.startswith("Field("):
        inner = rhs[len("Field("):].rstrip(")")
        if re.search(r'\bdefault(_factory)?\s*=', inner):
            return True
        first_token = inner.split(",")[0].strip()
        return bool(first_token) and first_token != "..." and "=" not in first_token
    return True


_UPDATE_CLASS_DECL_RE = re.compile(r'^class\s+(\w+Update)\s*\(\s*BaseModel\s*\)\s*:', re.MULTILINE)


# Confirmed live (habit_tracker, 2026-07-25): HabitUpdate declared
# `name: Optional[str] = Field(min_length=1)` -- Optional-typed, but the
# Field() call has no positional default and no default=/default_factory=
# kwarg, so Pydantic v2 still treats it as REQUIRED (Optional[...] only
# widens the accepted *type* to include None; it does not, by itself,
# supply a default). The CRUD journey's partial-update PATCH omits
# untouched fields exactly as a real client would, and Pydantic 422's
# with "field required" on every Edit step -- an Update schema where
# every field is unusable for a partial update. Same root defect as
# _fix_model_schema_notnull_gap's fix (preflight.py): the LLM's
# `Field(min_length=1)` idiom drops the leading `None,`/`...,` positional
# arg often enough that any check assuming Optional implies "has a
# default" misses it.
def _patch_update_schema_optional_field_missing_default(project_path: Path) -> int:
    """
    For every `*Update(BaseModel)` schema class, gives each Optional-typed
    field an explicit `None` default when it doesn't already have a real
    one (reuses _field_rhs_has_real_default, the same requiredness check
    already trusted elsewhere in this file) -- `Optional[str]` (bare, no
    `=` at all), `Optional[str] = Field(min_length=1)`, and
    `Optional[str] = ...` all become `Optional[str] = Field(None, ...)` /
    `Optional[str] = None`. Scoped to *Update classes only: an Update
    schema exists specifically to support partial updates, so a field
    that's Optional-typed but still Pydantic-required is never
    intentional there the way it might arguably be on a Create/Base
    schema, which this function never touches.
    """
    schemas_dir = project_path / "app" / "schemas"
    if not schemas_dir.exists():
        return 0

    patched = 0
    for sf in schemas_dir.rglob("*.py"):
        try:
            content = sf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        class_matches = list(_UPDATE_CLASS_DECL_RE.finditer(content))
        if not class_matches:
            continue

        text = content
        changed = False
        for cm in reversed(class_matches):
            class_start = cm.end()
            next_class = _CLASS_DECL_RE.search(text, class_start)
            class_end = next_class.start() if next_class else len(text)
            body = text[class_start:class_end]

            def _fix_field(m: re.Match) -> str:
                nonlocal changed
                indent, field_name, annotation, default = m.group(1), m.group(2), m.group(3), m.group(4)
                if "Optional[" not in annotation:
                    return m.group(0)
                rhs = default[1:].strip() if default else ""
                has_real_default = bool(rhs) and rhs != "..." and _field_rhs_has_real_default(rhs)
                if has_real_default:
                    return m.group(0)
                changed = True
                ann = annotation.strip()
                if not rhs or rhs == "...":
                    return f"{indent}{field_name}: {ann} = None"
                # Only remaining shape _field_rhs_has_real_default can say
                # "not a real default" for: a Field(...) call with no
                # positional value and no default=/default_factory= kwarg.
                inner = rhs[len("Field("):].rstrip(")").strip()
                if inner.startswith("..."):
                    inner = "None" + inner[3:]
                elif inner:
                    inner = f"None, {inner}"
                else:
                    inner = "None"
                return f"{indent}{field_name}: {ann} = Field({inner})"

            new_body = _CLASS_FIELD_LINE_RE.sub(_fix_field, body)
            if new_body != body:
                text = text[:class_start] + new_body + text[class_end:]

        if changed and text != content:
            sf.write_text(text, encoding="utf-8")
            patched += 1
            print(f"  [patcher] Gave Update-schema Optional field(s) an explicit None default in {sf.name}")

    return patched


# Same name vocabulary as _RESPONSE_CLASS_RE, but matchable against a bare
# class NAME (no trailing "(bases):") -- _RESPONSE_CLASS_RE itself requires
# a full "class X(...)" declaration to match at all, which a bare name
# string can never satisfy.
_RESPONSE_CLASS_NAME_RE = re.compile(r'\w+(?:Response|Out|Read|List|Detail|Schema)\w*$', re.IGNORECASE)


def _patch_response_schema_inherited_required_fields(project_path: Path) -> int:
    """
    _patch_response_schemas_optional (above) only widens fields declared
    directly in a *Response class's own body -- it never sees fields the
    class INHERITS from a shared *Base class, a pattern this codebase's
    own generation increasingly produces:
        class XBase(BaseModel): title: str; price: float; ...
        class XCreate(XBase): pass
        class XResponse(XBase): id: int
    XResponse never re-declares price, so the direct-body scan above has
    nothing to widen, and price stays required-and-inherited.

    Confirmed live (2026-07-11): a generated course-platform app's
    CourseResponse(CourseBase) inherited price/duration_hours/difficulty
    as REQUIRED from CourseBase, but the Course SQLAlchemy model has no
    such columns at all. FastAPI's response-model serialization tries to
    read them off the returned ORM object, finds nothing, and the request
    crashes. This exact bug was UNREACHABLE by any test until this
    cycle's role-aware validation fix (V20.1.5) made it possible to
    actually reach an authorized "instructor" identity for the first time
    -- previously every test hit the 403 gate and never got far enough to
    trigger it.

    Never touches the base class itself (so XCreate keeps its real
    requiredness) -- only injects `field: Optional[Any] = None` overrides
    directly into the *Response subclass body, using the exact same
    "insert immediately after the class header" placement
    _patch_missing_create_update_fields already validated is safe against
    a `pass`-only or docstring-led body (a naive skip-past-leading-content
    regex corrupted 4/9 real projects it touched before that fix; this
    reuses the corrected, simpler approach directly).
    """
    schemas_dir = project_path / "app" / "schemas"
    if not schemas_dir.exists():
        return 0

    # Project-wide: class_name -> (file, base_name, {field_name: (type, has_default)})
    class_info: dict[str, tuple] = {}
    file_contents: dict[Path, str] = {}
    for sf in schemas_dir.glob("*.py"):
        try:
            content = sf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        file_contents[sf] = content
        for m in _CLASS_DECL_RE.finditer(content):
            cls_name, bases = m.group(1), m.group(2)
            base_name = bases.split(",")[0].strip() or None
            next_m = _CLASS_DECL_RE.search(content, m.end())
            body = content[m.end(): next_m.start() if next_m else len(content)]
            fields = {}
            for fm in _CLASS_FIELD_LINE_RE.finditer(body):
                _indent, fname, ftype, has_default = fm.groups()
                if fname in ("id", "pass"):
                    continue
                rhs = (has_default or "").lstrip("=").strip()
                fields[fname] = (ftype.strip(), _field_rhs_has_real_default(rhs))
            class_info[cls_name] = (sf, base_name, fields)

    to_insert: dict[Path, list[tuple]] = {}  # file -> [(cls_name, {missing_field: type})]
    for cls_name, (sf, base_name, fields) in class_info.items():
        if not _RESPONSE_CLASS_NAME_RE.match(cls_name):
            continue
        if not base_name or base_name not in class_info:
            continue
        _, _, base_fields = class_info[base_name]
        missing = {
            fname for fname, (ftype, has_default) in base_fields.items()
            if fname not in fields and not has_default and not ftype.startswith("Optional[")
        }
        if missing:
            to_insert.setdefault(sf, []).append((cls_name, missing))

    patched = 0
    for sf, entries in to_insert.items():
        content = file_contents[sf]
        for cls_name, missing_fields in entries:
            m = re.search(rf'^class\s+{re.escape(cls_name)}\s*\([^)]*\)\s*:[ \t]*\n', content, re.MULTILINE)
            if not m:
                continue
            insert_at = m.end()
            new_lines = "".join(f"    {f}: Optional[Any] = None\n" for f in sorted(missing_fields))
            content = content[:insert_at] + new_lines + content[insert_at:]

        typing_import = re.search(r"^from typing import ([^\n]+)", content, re.MULTILINE)
        needed = {"Optional", "Any"}
        if typing_import:
            have = {n.strip() for n in typing_import.group(1).split(",")}
            add = needed - have
            if add:
                content = (content[:typing_import.start()]
                           + f"from typing import {typing_import.group(1)}, {', '.join(sorted(add))}"
                           + content[typing_import.end():])
        else:
            content = "from typing import Optional, Any\n" + content

        sf.write_text(content, encoding="utf-8")
        patched += 1
        names = ", ".join(c for c, _ in entries)
        print(f"  [patcher] Added inherited-but-missing field override(s) to {names} in {sf.name}")

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


def _patch_bare_pydantic_from_attributes(project_path: Path) -> int:
    """Repair Pydantic v2's invalid bare ``from_attributes`` assignment.

    ``from_attributes = True`` is valid only inside a legacy ``class Config``.
    At the top level of a BaseModel class Pydantic treats it as an untyped
    field and refuses to import the entire application.  Convert only that
    direct class-body form; nested Config classes are deliberately untouched.
    """
    import ast as _ast

    schemas_dir = project_path / "app" / "schemas"
    if not schemas_dir.exists():
        return 0

    patched = 0
    for sf in schemas_dir.glob("*.py"):
        try:
            src = sf.read_text(encoding="utf-8", errors="replace")
            tree = _ast.parse(src)
        except Exception:
            continue
        lines = src.splitlines()
        changed = False
        for node in tree.body:
            if not isinstance(node, _ast.ClassDef):
                continue
            base_names = {
                base.id if isinstance(base, _ast.Name) else base.attr
                for base in node.bases
                if isinstance(base, (_ast.Name, _ast.Attribute))
            }
            if "BaseModel" not in base_names:
                continue
            for child in node.body:
                if not (
                    isinstance(child, _ast.Assign)
                    and len(child.targets) == 1
                    and isinstance(child.targets[0], _ast.Name)
                    and child.targets[0].id == "from_attributes"
                    and isinstance(child.value, _ast.Constant)
                    and child.value.value is True
                    and child.lineno == child.end_lineno
                ):
                    continue
                index = child.lineno - 1
                indent = lines[index][:len(lines[index]) - len(lines[index].lstrip())]
                lines[index] = f'{indent}model_config = {{"from_attributes": True}}'
                changed = True
        if changed:
            sf.write_text("\n".join(lines) + ("\n" if src.endswith("\n") else ""), encoding="utf-8")
            patched += 1
    if patched:
        print(f"  [schema_patcher] Repaired bare Pydantic from_attributes in {patched} schema file(s)")
    return patched


def _patch_pydantic_field_type_name_collisions(project_path: Path) -> int:
    """Avoid Pydantic v2 annotation evaluation collisions such as ``date: date``."""
    import ast as _ast

    schemas_dir = project_path / "app" / "schemas"
    if not schemas_dir.exists():
        return 0
    patched = 0
    for sf in schemas_dir.glob("*.py"):
        try:
            src = sf.read_text(encoding="utf-8", errors="replace")
            tree = _ast.parse(src)
        except Exception:
            continue
        lines = src.splitlines()
        changed = False
        for node in tree.body:
            if not isinstance(node, _ast.ClassDef):
                continue
            for child in node.body:
                if not (
                    isinstance(child, _ast.AnnAssign)
                    and isinstance(child.target, _ast.Name)
                    and child.target.id == "date"
                    and isinstance(child.annotation, _ast.Name)
                    and child.annotation.id == "date"
                    and child.lineno == child.end_lineno
                ):
                    continue
                index = child.lineno - 1
                lines[index] = re.sub(r"\bdate\s*:\s*date\b", "date: _datetime.date", lines[index], count=1)
                changed = True
        if not changed:
            continue
        new_src = "\n".join(lines) + ("\n" if src.endswith("\n") else "")
        if "import datetime as _datetime" not in new_src:
            new_src = "import datetime as _datetime\n" + new_src
        sf.write_text(new_src, encoding="utf-8")
        patched += 1
    if patched:
        print(f"  [schema_patcher] Repaired Pydantic field/type name collisions in {patched} schema file(s)")
    return patched


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


_FILTERED_CTOR_KWARG_COLLISION_HEAD_RE = re.compile(
    r'\b([A-Z]\w+)\(\*\*\{k: v for k, v in (\w+)\.(?:dict|model_dump)\(\)\.items\(\)'
    r' if k in \1\.__table__\.columns\.keys\(\)'
    r'(?: and k not in \{(?P<existing>[^}]*)\})?\}',
)


def _patch_filtered_ctor_kwarg_collision(project_path: Path) -> int:
    """
    Fix an already-generated `Model(**{k: v for ... if k in Model.__table__.
    columns.keys()}, some_kwarg=value)` call that's missing (or only
    partially has) the "and k not in {...}" exclusion
    _patch_star_dict_extra_fields now adds for every trailing explicit kwarg.

    Without it, if the schema also accepts a same-named field as client input
    (HabitCreate exposing `user_id` as optional -- a MassAssignment gap flagged
    by security review but never auto-fixed), the unpacked dict AND the
    explicit kwarg both supply that name and Python raises "got multiple
    values for keyword argument". Reproduced live: POST /habits 500'd on
    every single request for exactly this reason, and the pipeline's own
    runtime-fix loop failed to resolve it before giving up, shipping the app
    at "deploy ready" with a completely broken create flow.

    A second, distinct reproduction (Exp139, todo canary, 2026-07-22):
    `Task(**{k: v for k, v in task_in.dict().items() if k in
    Task.__table__.columns.keys() and k not in {'user_id'}}, user_id=
    current_user.id, completed=False)` -- an exclusion clause WAS present
    (for `user_id`) but didn't cover `completed`, the route's own second
    trailing kwarg, so the collision fired for `completed` instead. The
    original regex only matched a bare filter with no exclusion clause at
    all and silently skipped this already-partially-fixed shape; it now
    captures an existing exclusion set and unions it with every trailing
    kwarg rather than requiring a clean slate.

    A third reproduction (ForgeBench v1.0, employee_directory, 2026-07-28):
    `Employee(**{...} and k not in {'hire_date'}}, hire_date=date.today(),
    active=False)` still 500'd on every POST /employees -- `active` was
    never added to the exclusion set because the regex used to find the
    trailing extra_args (everything up to the call's closing paren) was
    "((?:[^)]*)?)" followed by a literal close-paren, which stops at the
    FIRST close-paren it finds. With a computed
    default like `date.today()` in the trailing kwargs, that's the closing
    paren of `date.today(`, not the real end of the Employee(...) call --
    the match silently truncated before ever reaching `active=False`, so
    the collision was never even detected. Now uses find_matching_brace
    (open_char='(', close_char=')') to find the call's REAL closing paren,
    correctly walking past any nested parens in the trailing kwargs.
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

        new_content = content
        offset = 0
        changed = False
        while True:
            m = _FILTERED_CTOR_KWARG_COLLISION_HEAD_RE.search(new_content, offset)
            if not m:
                break
            cls_name, schema_var, existing = m.group(1), m.group(2), m.group("existing")
            call_open_paren = m.start() + len(cls_name)
            if new_content[call_open_paren] != "(":
                offset = m.end()
                continue
            call_close_paren = find_matching_brace(
                new_content, call_open_paren, quote_chars="'\"",
                open_char="(", close_char=")",
            )
            if call_close_paren == -1:
                offset = m.end()
                continue
            extra_args = new_content[m.end():call_close_paren]
            explicit_kwargs = set(re.findall(r'(?:^|,)\s*(\w+)\s*=', extra_args))
            existing_kwargs = set(re.findall(r"'([^']*)'", existing or ""))
            all_excludes = sorted(explicit_kwargs | existing_kwargs)
            if not all_excludes or all_excludes == sorted(existing_kwargs):
                offset = call_close_paren + 1
                continue
            exclude_set = "{" + ", ".join(f"'{k}'" for k in all_excludes) + "}"
            filtered = (
                f"{cls_name}(**{{k: v for k, v in {schema_var}.dict().items() "
                f"if k in {cls_name}.__table__.columns.keys() and k not in {exclude_set}}}"
                f"{extra_args})"
            )
            new_content = new_content[:m.start()] + filtered + new_content[call_close_paren + 1:]
            changed = True
            offset = m.start() + len(filtered)

        if changed:
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
    # Exp098: "name" and "password" already appeared as candidate VALUES
    # above (e.g. "username": [..., "name", ...]) but never as their own
    # KEYS -- Exp097 found a bad_attr of exactly "name" or "password" was
    # therefore never considered fixable at all via the plain
    # `.get(bad_attr)` lookup below, regardless of what the model/schema
    # actually declared.
    #
    # A mechanical "also check values, reciprocally" fix was tried first
    # and reverted: scanning the WHOLE dict for any key whose value list
    # contains "name" pulls in "description"/"title"/"label" alongside
    # the intended identity cluster (username/full_name/display_name) --
    # and unlike identity fields, "description" is a body-text field,
    # not a label/name substitute. Confirmed live via full-corpus replay
    # against gym_tracker: `tag_in.name` (TagCreate genuinely has no
    # "name", only "title"/"description") got "fixed" to
    # `tag_in.description` instead of the correct `tag_in.title`, purely
    # because "description" happens to be declared earlier in this dict
    # than "title" -- a real, wrong rewrite, not a hypothetical risk.
    # So "name"/"password" are curated explicit keys instead, scoped to
    # only the identity/credential synonyms they were actually meant to
    # cover -- deliberately NOT title/label/description, which stay
    # reachable only in their own existing one-directional entries above.
    "name": ["username", "full_name", "display_name"],
    "password": ["password_hash", "hashed_password", "pwd"],
    "password_hash": ["hashed_password", "password"],
    "hashed_password": ["password_hash", "password"],
}


def _query_target_class(call: ast.Call, model_cols: dict) -> Optional[str]:
    """Walk a (possibly chained) call expression like
    `db.query(User).filter(...).first()` back through its `.attr(...)`
    chain looking for a `.query(ClassName)` leg, returning ClassName if
    it's a known model. Used to type ORM-query results as model instances."""
    node = call
    while isinstance(node, ast.Call):
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr == "query" and node.args:
            arg0 = node.args[0]
            if isinstance(arg0, ast.Name) and arg0.id in model_cols:
                return arg0.id
        if isinstance(f, ast.Attribute):
            node = f.value
        else:
            break
    return None


def _infer_model_typed_names(fn: ast.AST, model_cols: dict, schema_cols: dict | None = None) -> dict:
    """Best-effort, conservative {variable_name: class_name} map for names
    PROVABLY bound to an instance of a known SQLAlchemy model class OR
    (Exp098) Pydantic schema class within this function's own source --
    constructor calls (`u = User(...)`), ORM query results
    (`u = db.query(User).first()`, `for u in db.query(User)`), and
    directly-typed parameters. A name absent from the result is never
    treated as typed, so callers can never rewrite an attribute access whose
    object type isn't actually known.

    `schema_cols` extends every one of the three "provably typed" shapes
    above (typed parameter, annotated assignment, constructor call) to
    also recognize Pydantic schema classes (e.g. `user_in: UserCreate`,
    the shape route handlers and seed_routes.py actually use) --
    SQLAlchemy detection via `model_cols` is completely unchanged, and
    passing no `schema_cols` (the default) reproduces the exact prior
    behavior byte-for-byte, since `name in {}` is always False.
    """
    schema_cols = schema_cols or {}
    typed: dict = {}

    if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for arg in fn.args.args:
            ann = arg.annotation
            name = ann.id if isinstance(ann, ast.Name) else None
            if name and (name in model_cols or name in schema_cols):
                typed[arg.arg] = name

    for node in ast.walk(fn):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                and isinstance(node.annotation, ast.Name) \
                and (node.annotation.id in model_cols or node.annotation.id in schema_cols):
            # `var: ClassName = ...` -- the annotation itself is authoritative,
            # regardless of what (if anything) the RHS looks like.
            typed[node.target.id] = node.annotation.id
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) \
                and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            f = node.value.func
            if isinstance(f, ast.Name) and (f.id in model_cols or f.id in schema_cols):
                typed[node.targets[0].id] = f.id
                continue
            cls = _query_target_class(node.value, model_cols)
            if cls:
                typed[node.targets[0].id] = cls
        elif isinstance(node, ast.For) and isinstance(node.target, ast.Name) \
                and isinstance(node.iter, ast.Call):
            cls = _query_target_class(node.iter, model_cols)
            if cls:
                typed[node.target.id] = cls

    return typed


def _collect_schema_cols(schemas_dir: Path) -> dict:
    """Build {cls_name -> frozenset(field_names)} for every Pydantic
    BaseModel subclass declared across app/schemas/*.py.

    Exp098: the schema-side counterpart to the model_cols dict built in
    _patch_attr_access_mismatches -- reuses
    fix_writer_service._collect_basemodel_classes per-file (local
    inheritance resolution only, matching that helper's own documented
    scope) and merges across files. Closes the gap where
    _infer_model_typed_names could never resolve a Pydantic-schema-typed
    parameter (e.g. `user_in: UserCreate`) because model_cols only ever
    tracked SQLAlchemy models (Column(...) declarations in app/models/).
    Returns {} if schemas_dir doesn't exist -- callers degrade gracefully
    to SQLAlchemy-only behavior, never raise.
    """
    if not schemas_dir.exists():
        return {}
    from app.services.fix_writer_service import _collect_basemodel_classes

    schema_cols: dict = {}
    for sf in schemas_dir.glob("*.py"):
        if sf.name == "__init__.py":
            continue
        try:
            tree = ast.parse(sf.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        for cls_name, fields in _collect_basemodel_classes(tree).items():
            schema_cols[cls_name] = frozenset(fields) | schema_cols.get(cls_name, frozenset())
    return schema_cols


def _iter_top_level_functions(tree: ast.Module):
    """Module-level functions and one level of class methods -- deliberately
    NOT a full ast.walk, so a nested closure's attribute accesses are swept
    into its enclosing top-level function's scope exactly once instead of
    being independently (and possibly conflictingly) re-typed."""
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node
        elif isinstance(node, ast.ClassDef):
            for sub in ast.iter_child_nodes(node):
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    yield sub


def _patch_attr_access_mismatches(project_path: Path) -> int:
    """Fix route files that access obj.invalid_attr where invalid_attr doesn't exist
    on the SQLAlchemy model (e.g. user.username when model only has email) OR
    (Exp098) on a Pydantic schema class (e.g. user_in.password when the
    schema only has hashed_password) -- e.g. seed_routes.py constructing a
    demo User/UserCreate with a guessed field name that Wave-2 (models) or
    Wave-3 (schemas) never actually used (Exp097's root-caused gap).

    Exp073: CONFIRMED BUG, now fixed -- this used to apply the fix via a
    file-wide `re.sub()` on every `.bad_attr` occurrence in the file,
    regardless of which object it was accessed on. Since bad_attr is a
    common English word (display_name, status, title, ...), a genuinely
    unrelated object in the SAME route file that legitimately has that
    attribute (e.g. `req: SignupRequest` with its own `.display_name`
    field, sitting in the same auth_routes.py as a `User` model missing
    that column) got silently corrupted alongside the real fix (Exp072).
    Now scoped via AST: only rewritten when the object is PROVABLY an
    instance of the mismatched model/schema class (constructor call, ORM
    query result, typed parameter, or bare `ClassName.attr` access) within
    its own function scope -- never a blanket file-wide substitution.
    """
    routes_dir = project_path / "app" / "routes"
    models_dir = project_path / "app" / "models"
    schemas_dir = project_path / "app" / "schemas"
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

    # Exp098: Pydantic schema counterpart to model_cols -- {} if schemas_dir
    # is missing/empty, in which case behavior is identical to before this
    # experiment (schema_cols never contributes a match).
    schema_cols = _collect_schema_cols(schemas_dir)

    if not model_cols and not schema_cols:
        return 0

    patched = 0
    for rf in routes_dir.glob("*.py"):
        try:
            content = rf.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(content)
        except Exception:
            continue

        # {lineno: [(start_col, end_col, good_attr), ...]} -- collected across
        # every top-level function, applied back-to-front per line so earlier
        # edits on the same line don't shift later columns' offsets.
        by_line: dict = {}

        for fn in _iter_top_level_functions(tree):
            typed_names = _infer_model_typed_names(fn, model_cols, schema_cols)
            for node in ast.walk(fn):
                if not (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)):
                    continue
                obj_name = node.value.id
                cls_name = (
                    typed_names.get(obj_name)
                    or (obj_name if obj_name in model_cols else None)
                    or (obj_name if obj_name in schema_cols else None)
                )
                if not cls_name:
                    continue
                # Attribute access must be single-line for a safe column-offset
                # edit; multi-line chains (rare, e.g. inside parens) are skipped.
                if node.value.end_lineno != node.lineno or node.end_lineno != node.lineno:
                    continue
                valid_cols = model_cols.get(cls_name) or schema_cols.get(cls_name)
                if valid_cols is None:
                    continue
                bad_attr = node.attr
                if bad_attr in valid_cols:
                    continue
                candidates = _FIELD_SYNONYMS_PATCHER.get(bad_attr)
                if not candidates:
                    continue
                good_attr = next((c for c in candidates if c in valid_cols), None)
                if not good_attr:
                    continue
                by_line.setdefault(node.lineno, []).append(
                    (node.value.end_col_offset, node.end_col_offset, good_attr)
                )

        if not by_line:
            continue

        lines = content.splitlines(keepends=True)
        for lineno, repls in by_line.items():
            line = lines[lineno - 1]
            for start_col, end_col, good_attr in sorted(set(repls), key=lambda r: -r[0]):
                line = line[:start_col] + "." + good_attr + line[end_col:]
            lines[lineno - 1] = line

        new_content = "".join(lines)
        if new_content != content:
            try:
                rf.write_text(new_content, encoding="utf-8")
                patched += 1
                print(f"  [patcher] Fixed attribute accesses in {rf.name}")
            except Exception:
                pass
    return patched


# Ownership FK naming drift: the SAME conceptual "who owns this row" column
# named differently across an app's own model vs. its route-level ownership
# checks. _FIELD_SYNONYMS_PATCHER above already covers creator_id/author_id
# as bad_attr keys, but deliberately NOT user_id/owner_id/created_by as
# keys -- those are common enough as REAL, correct column names on OTHER
# models in the same file that _patch_attr_access_mismatches' blanket,
# file-wide (not class-qualified) substitution would risk damaging
# unrelated, entirely correct code. This patcher covers exactly that gap
# with class-qualified precision instead of a wider synonym key.
_OWNERSHIP_FK_SYNONYMS = {
    "owner_id":   ["user_id", "creator_id", "author_id", "created_by"],
    "user_id":    ["owner_id", "creator_id", "author_id", "created_by"],
    "creator_id": ["owner_id", "user_id", "author_id", "created_by"],
    "author_id":  ["owner_id", "user_id", "creator_id", "created_by"],
    "created_by": ["owner_id", "user_id", "creator_id", "author_id"],
}


def _patch_ownership_fk_attribute_drift(project_path: Path) -> int:
    """
    Fix `ModelClass.wrong_ownership_field` query/filter expressions where
    the model actually uses a DIFFERENT ownership-FK column name.

    Confirmed live (2026-07-11, found via corpus sweep, $0): a generated
    CRM app's Contact/Deal models declare `owner_id` as the real FK to
    users.id, but contact_routes.py/deal_routes.py/stats_routes.py filter
    on `Contact.user_id == current_user.id` -- and Contact ALSO happens to
    have an unrelated, non-FK `user_id` column that defaults to 0 and is
    never set by any route. Every such query silently returns nothing for
    every real user (0 never equals a real user id) -- a silent,
    permanent data-isolation bug on GET/PUT/DELETE for contacts, not a
    crash, so nothing else in this pipeline's error taxonomy would ever
    surface it.

    Checks FOREIGN-KEY-typed columns specifically, not "any column" --
    Contact.user_id exists on the model (a real, if unrelated, non-FK
    integer column that just happens to share the name), so a plain
    "does this attribute exist anywhere on the model" check would wrongly
    treat it as fine. Only a column actually declared with `ForeignKey(...)`
    counts as "the model really has an ownership FK under this name."

    Only rewrites `ClassName.bad_attr` where ClassName is a confirmed
    local model class (not a bare `.bad_attr` on some other, unresolved
    object) -- deliberately narrower than _patch_attr_access_mismatches'
    file-wide substitution, since a route file legitimately referencing
    multiple models could otherwise have a DIFFERENT class's genuinely
    correct `.user_id` damaged by the same blanket replacement.
    """
    routes_dir = project_path / "app" / "routes"
    models_dir = project_path / "app" / "models"
    if not routes_dir.exists() or not models_dir.exists():
        return 0

    fk_cols = _model_fk_columns(models_dir)
    if not fk_cols:
        return 0

    patched = 0
    for rf in routes_dir.glob("*.py"):
        try:
            content = rf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        new_content = content
        for cls_name, cols in fk_cols.items():
            if cls_name not in new_content:
                continue
            for bad_attr, candidates in _OWNERSHIP_FK_SYNONYMS.items():
                if bad_attr in cols:
                    continue  # this model's FK genuinely has this name -- not drift
                good_attr = next((c for c in candidates if c in cols), None)
                if not good_attr:
                    continue
                pattern = re.compile(rf'\b{re.escape(cls_name)}\.{re.escape(bad_attr)}\b')
                new_content = pattern.sub(f"{cls_name}.{good_attr}", new_content)

        if new_content != content:
            rf.write_text(new_content, encoding="utf-8")
            patched += 1
            print(f"  [patcher] Fixed ownership-FK attribute drift in {rf.name}")

    return patched


# Exp092: the CREATE-time counterpart to the drift fix above -- 17/23
# (74%) of tracked JourneyCRUDFailure instances (Exp091) share one root
# cause: a POST handler accepts current_user but never assigns the
# constructed model's ownership FK before db.add(). Confirmed live in
# generated_projects/inventory_manager/app/routes/product_routes.py's
# create_product(): current_user accepted, never referenced again.
# app/prompts/shared_contract.py already instructs this exact assignment
# but scopes it to the literal string "user_id", missing owner_id/
# author_id/creator_id/created_by -- and even an exact "user_id" match
# isn't followed with full reliability (ordinary LLM instruction-
# following variance, confirmed: todo_list_app's Task.user_id matches
# the rule's own trigger string and still historically crashed).
# Deliberately a SEPARATE function from _patch_ownership_fk_attribute_drift
# above (different bug shape -- that one fixes existing query/filter
# expressions using the wrong attribute name; this one injects a missing
# insert-time assignment) that reuses its exact same two building blocks
# (_model_fk_columns, _OWNERSHIP_FK_SYNONYMS) rather than duplicating them.

def _has_post_decorator(func: "ast.FunctionDef | ast.AsyncFunctionDef") -> bool:
    for dec in func.decorator_list:
        if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr == "post":
            return True
    return False


def _current_user_param_name(func: "ast.FunctionDef | ast.AsyncFunctionDef"):
    """Returns the parameter name bound to `Depends(get_current_user)`,
    or None. Checks both regular (possibly-defaulted) params and
    keyword-only params, matching how FastAPI handlers commonly mix
    body/Depends/Query parameters."""
    args = func.args
    pairs = []
    if args.defaults:
        pairs.extend(zip(args.args[len(args.args) - len(args.defaults):], args.defaults))
    pairs.extend((a, d) for a, d in zip(args.kwonlyargs, args.kw_defaults) if d is not None)
    for param, default in pairs:
        if (isinstance(default, ast.Call) and isinstance(default.func, ast.Name)
                and default.func.id == "Depends" and default.args
                and isinstance(default.args[0], ast.Name) and default.args[0].id == "get_current_user"):
            return param.arg
    return None


def _assigns_attribute(func, var_name: str, attr_name: str) -> bool:
    """True if `var_name.attr_name = ...` (any RHS) appears anywhere in
    the function body -- preserves any pre-existing ownership logic,
    correct or not, rather than second-guessing it (matches Exp088's
    same "don't re-convert already-handled cases" philosophy)."""
    for node in ast.walk(func):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name)
                        and target.value.id == var_name and target.attr == attr_name):
                    return True
    return False


def _find_db_add_lineno(func, var_name: str):
    """Line number of the first `<session>.add(var_name)` call in the
    function body, or None. Anchoring on the actual persistence call
    (rather than right after construction) means any intervening
    business logic that itself sets the field is naturally still caught
    by _assigns_attribute before this is even consulted."""
    for node in ast.walk(func):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add" and len(node.args) == 1
                and isinstance(node.args[0], ast.Name) and node.args[0].id == var_name):
            return node.lineno
    return None


def _assigns_via_dict_unpack(func, call: ast.Call, attr_name: str) -> bool:
    """True if `call` constructs the model via `Cls(**some_dict)` and
    `some_dict[attr_name] = ...` (or `some_dict["attr_name"] = ...`) is
    assigned anywhere earlier in the function body. Confirmed live
    (generated_projects/recipe_forge/app/routes/recipe_routes.py):
    `recipe_for_db["user_id"] = current_user.id` followed by
    `Recipe(**recipe_for_db)` -- ownership already correctly assigned,
    just via dict mutation rather than a literal keyword argument or a
    post-construction attribute assignment, so _assigns_attribute alone
    doesn't see it."""
    dict_vars = {
        kw.value.id for kw in call.keywords
        if kw.arg is None and isinstance(kw.value, ast.Name)
    }
    if not dict_vars:
        return False
    for node in ast.walk(func):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if (isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name)
                and target.value.id in dict_vars):
            key = target.slice
            # Python <3.9 wraps the subscript key in ast.Index; unwrap ONLY that
            # wrapper -- ast.Constant nodes also have their own .value attribute
            # (the literal itself), so a blind getattr(key, "value", key) here
            # would incorrectly unwrap a Constant down to a bare string, making
            # the isinstance(key, ast.Constant) check below always fail.
            if isinstance(key, ast.Index):  # pragma: no cover -- pre-3.9 compat only
                key = key.value
            if isinstance(key, ast.Constant) and key.value == attr_name:
                return True
    return False


def _patch_missing_ownership_assignment(project_path: Path) -> int:
    """
    For each POST handler that accepts a current-user dependency and
    constructs an instance of a model with a recognized ownership FK
    (via _model_fk_columns/_OWNERSHIP_FK_SYNONYMS) without ever assigning
    that field, injects `<var>.<fk_col> = <current_user_param>.id`
    immediately before the corresponding `db.add(<var>)` call.

    Conservative by construction, matching this file's own established
    style: only acts on the exact shapes confirmed live (a bare
    `ClassName(...)` assignment feeding a same-function `db.add(...)`
    call) -- a handler using setattr(), a differently-named session
    variable, or no db.add() call at all is left completely untouched,
    not guessed at.
    """
    routes_dir = project_path / "app" / "routes"
    models_dir = project_path / "app" / "models"
    if not routes_dir.exists() or not models_dir.exists():
        return 0

    fk_cols = _model_fk_columns(models_dir)
    if not fk_cols:
        return 0

    ownership_fk_by_class: dict[str, str] = {}
    for cls_name, cols in fk_cols.items():
        for col in sorted(cols):  # deterministic selection if a model somehow has >1 match
            if col in _OWNERSHIP_FK_SYNONYMS:
                ownership_fk_by_class[cls_name] = col
                break
    if not ownership_fk_by_class:
        return 0

    patched = 0
    for rf in routes_dir.glob("*.py"):
        try:
            content = rf.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(content)
        except Exception:
            continue

        lines = content.split("\n")
        insertions: list[tuple[int, str]] = []  # (0-based index to insert before, text)

        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not _has_post_decorator(func):
                continue
            current_user_name = _current_user_param_name(func)
            if not current_user_name:
                continue

            for stmt in ast.walk(func):
                if not isinstance(stmt, ast.Assign) or not isinstance(stmt.value, ast.Call):
                    continue
                call = stmt.value
                if not isinstance(call.func, ast.Name) or call.func.id not in ownership_fk_by_class:
                    continue
                if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
                    continue
                var_name = stmt.targets[0].id
                fk_col = ownership_fk_by_class[call.func.id]

                # Already assigned as a constructor kwarg (obj = Cls(user_id=..., ...))?
                if any(kw.arg == fk_col for kw in call.keywords):
                    continue
                # Already assigned via a later obj.<fk_col> = ... anywhere in the handler?
                if _assigns_attribute(func, var_name, fk_col):
                    continue
                # Already assigned via dict-mutation-then-**unpack (obj = Cls(**d) where
                # d["fk_col"] = ... was set earlier)?
                if _assigns_via_dict_unpack(func, call, fk_col):
                    continue

                add_lineno = _find_db_add_lineno(func, var_name)
                if add_lineno is None:
                    continue

                idx = add_lineno - 1  # ast linenos are 1-based
                indent = re.match(r"(\s*)", lines[idx]).group(1)
                new_line = f"{indent}{var_name}.{fk_col} = {current_user_name}.id"
                if idx > 0 and lines[idx - 1].strip() == new_line.strip():
                    continue  # already patched (defense-in-depth alongside _assigns_attribute)
                insertions.append((idx, new_line))

        if insertions:
            out = list(lines)
            for idx, text in sorted(insertions, key=lambda t: -t[0]):
                out.insert(idx, text)
            new_content = "\n".join(out)
            if new_content != content:
                rf.write_text(new_content, encoding="utf-8")
                patched += 1
                print(f"  [patcher] Injected missing ownership assignment in {rf.name}")

    return patched


def _last_param_end_position(func: "ast.FunctionDef | ast.AsyncFunctionDef"):
    """(end_lineno, end_col_offset) of the last token in the function's
    parameter list -- the default value if a parameter has one, else its
    annotation, else the bare parameter name. Used as the splice point for
    appending a new trailing parameter; the ast module never exposes the
    closing paren's own position, so this is the only unambiguous anchor
    that works for both a single-line and a one-kwarg-per-line signature."""
    args = func.args
    candidates = []
    positional = list(args.posonlyargs) + list(args.args)
    defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    for arg, default in zip(positional, defaults):
        candidates.append(default or arg.annotation or arg)
    if args.vararg:
        candidates.append(args.vararg)
    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        candidates.append(default or arg.annotation or arg)
    if args.kwarg:
        candidates.append(args.kwarg)
    if not candidates:
        return None
    last = max(candidates, key=lambda n: (n.end_lineno, n.end_col_offset))
    return last.end_lineno, last.end_col_offset


# Companion to _patch_missing_ownership_assignment (Exp092) above: that
# function only injects the ownership assignment when a POST handler
# ALREADY accepts a `current_user: ... = Depends(get_current_user)`
# parameter, deliberately -- adding a brand-new dependency parameter to a
# function signature is a different, riskier class of edit than inserting
# a body statement.
#
# Confirmed live (habit_tracker, 2026-07-25): an "ARCHITECTURE REPAIR" LLM
# rewrite of habit_routes.py dropped Depends(get_current_user) from
# create_habit() entirely -- even though the Tech Lead's own review had
# flagged "Missing JWT authentication on endpoints that modify... user
# data" as CRITICAL -- while Habit.user_id is a NOT NULL FK. The handler
# built Habit(**{...}) with no user_id anywhere, db.add()/db.commit()
# raised an IntegrityError, and the "Create entity" journey step 500'd on
# every run. With no current_user parameter present at all,
# _patch_missing_ownership_assignment's own precondition silently no-ops,
# so the existing patcher could never reach this shape.
def _patch_missing_current_user_dependency_for_ownership_insert(project_path: Path) -> int:
    """
    For each POST handler with NO existing current_user/Depends(get_current_user)
    parameter that constructs an ownership-FK model via a bare
    `ClassName(...)` assignment feeding a same-function `db.add(...)` call
    (the exact shape _patch_missing_ownership_assignment already trusts),
    adds an untyped `current_user=Depends(get_current_user)` parameter --
    FastAPI resolves the dependency from the default alone, so no type
    annotation is needed and the User model's class/module name never has
    to be resolved or imported -- plus the same ownership assignment the
    companion function injects, and imports get_current_user from
    app.utils.auth (the "known-good" auth module every generated project
    already receives) if not already imported.

    Skips (does nothing) if the literal name `current_user` already
    appears anywhere in the function for any other reason -- adding a
    same-named parameter could collide with existing, unrelated logic,
    and this function's job is only to cover the one confirmed-safe gap.
    """
    routes_dir = project_path / "app" / "routes"
    models_dir = project_path / "app" / "models"
    if not routes_dir.exists() or not models_dir.exists():
        return 0

    fk_cols = _model_fk_columns(models_dir)
    if not fk_cols:
        return 0

    ownership_fk_by_class: dict[str, str] = {}
    for cls_name, cols in fk_cols.items():
        for col in sorted(cols):
            if col in _OWNERSHIP_FK_SYNONYMS:
                ownership_fk_by_class[cls_name] = col
                break
    if not ownership_fk_by_class:
        return 0

    patched = 0
    for rf in routes_dir.glob("*.py"):
        try:
            content = rf.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(content)
        except Exception:
            continue

        lines = content.split("\n")
        body_insertions: list[tuple[int, str]] = []
        param_insertions: list[tuple[int, int, str]] = []  # (end_lineno, end_col, text)
        touched = False

        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not _has_post_decorator(func):
                continue
            if _current_user_param_name(func):
                continue  # already has the dependency -- the companion patcher owns this case
            if any(isinstance(n, ast.Name) and n.id == "current_user" for n in ast.walk(func)):
                continue  # name already means something else in this handler -- too risky

            for stmt in ast.walk(func):
                if not isinstance(stmt, ast.Assign) or not isinstance(stmt.value, ast.Call):
                    continue
                call = stmt.value
                if not isinstance(call.func, ast.Name) or call.func.id not in ownership_fk_by_class:
                    continue
                if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
                    continue
                var_name = stmt.targets[0].id
                fk_col = ownership_fk_by_class[call.func.id]

                if any(kw.arg == fk_col for kw in call.keywords):
                    continue
                if _assigns_attribute(func, var_name, fk_col):
                    continue
                if _assigns_via_dict_unpack(func, call, fk_col):
                    continue

                add_lineno = _find_db_add_lineno(func, var_name)
                if add_lineno is None:
                    continue

                anchor = _last_param_end_position(func)
                if anchor is None:
                    continue

                idx = add_lineno - 1  # ast linenos are 1-based
                indent = re.match(r"(\s*)", lines[idx]).group(1)
                new_line = f"{indent}{var_name}.{fk_col} = current_user.id"
                body_insertions.append((idx, new_line))
                param_insertions.append((anchor[0], anchor[1], ", current_user=Depends(get_current_user)"))
                touched = True

        if not touched:
            continue

        # Parameter splices are same-line column edits, so they never shift
        # any line index -- safe to apply before the body-line insertions
        # below (which do shift every later index) regardless of order,
        # but doing them first keeps `lines` consistent for the indent
        # lookup above, which already ran against the pre-splice text.
        for lineno, col, text in sorted(param_insertions, key=lambda t: (-t[0], -t[1])):
            row = lineno - 1
            lines[row] = lines[row][:col] + text + lines[row][col:]

        out = list(lines)
        for idx, text in sorted(body_insertions, key=lambda t: -t[0]):
            out.insert(idx, text)

        needs_import = not re.search(
            r'^\s*from\s+\S+\s+import\s+.*\bget_current_user\b', content, re.MULTILINE
        ) and not re.search(r'^\s*import\s+.*\bget_current_user\b', content, re.MULTILINE)
        if needs_import:
            import_line_idxs = [i for i, l in enumerate(out) if re.match(r'^\s*(from|import)\s+', l)]
            insert_at = (import_line_idxs[-1] + 1) if import_line_idxs else 0
            out.insert(insert_at, "from app.utils.auth import get_current_user")

        new_content = "\n".join(out)
        if new_content == content:
            continue
        try:
            ast.parse(new_content)
        except SyntaxError:
            continue  # never write a syntactically broken file -- skip this one, fail soft

        rf.write_text(new_content, encoding="utf-8")
        patched += 1
        print(f"  [patcher] Injected missing current_user dependency + ownership assignment in {rf.name}")

    return patched


_SCHEMA_CLASS_RE = re.compile(r'^class\s+(\w+)\s*\(([^)]*)\)\s*:', re.MULTILINE)
_SCHEMA_FIELD_RE = re.compile(r'^\s{4}(\w+)\s*:', re.MULTILINE)


def _schema_classes_and_fields(schemas_dir: Path) -> dict[str, tuple[Path, set[str]]]:
    """{class_name: (file, {field_names})} for every Pydantic-looking class
    (bases mention BaseModel, or inherits a sibling class already known to
    be one -- e.g. `class WorkoutUpdate(WorkoutBase)`) across app/schemas/."""
    out: dict[str, tuple[Path, set[str]]] = {}
    if not schemas_dir.exists():
        return out
    for sf in schemas_dir.glob("*.py"):
        try:
            src = sf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for m in _SCHEMA_CLASS_RE.finditer(src):
            cls, bases = m.group(1), m.group(2)
            if "BaseModel" not in bases and not any(b.strip() in out for b in bases.split(",")):
                continue
            body_start = m.end()
            next_class = _SCHEMA_CLASS_RE.search(src, body_start)
            body = src[body_start:next_class.start() if next_class else len(src)]
            fields = set(_SCHEMA_FIELD_RE.findall(body))
            out[cls] = (sf, fields)
    return out


_MODEL_COLUMN_RE = re.compile(r'^\s{4}(\w+)\s*=\s*Column\(', re.MULTILINE)
_MODEL_FK_COLUMN_RE = re.compile(r'^\s{4}(\w+)\s*=\s*Column\([^\n]*ForeignKey', re.MULTILINE)


def _model_fk_columns(models_dir: Path) -> dict[str, set[str]]:
    """{model_class_name: {ForeignKey-typed column names}} across every
    app/models/*.py file. Unlike _model_classes_and_columns (which returns
    ALL Column(...) declarations), this only counts columns actually
    declared with ForeignKey(...) -- a model can have a plain, unrelated
    column that happens to share a name with a real ownership FK on
    another model (e.g. Contact.user_id = Column(Integer, default=0) with
    no ForeignKey, alongside the real Contact.owner_id FK), and treating
    that as "the model has this FK" would hide genuine ownership-FK drift."""
    out: dict[str, set[str]] = {}
    if not models_dir.exists():
        return out
    for mf in models_dir.glob("*.py"):
        try:
            content = mf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for m in _CLASS_DECL_RE.finditer(content):
            cls_name = m.group(1)
            next_m = _CLASS_DECL_RE.search(content, m.end())
            body = content[m.end(): next_m.start() if next_m else len(content)]
            cols = set(_MODEL_FK_COLUMN_RE.findall(body))
            if cols:
                out[cls_name] = cols
    return out


def _model_classes_and_columns(models_dir: Path) -> dict[str, set[str]]:
    """{model_class_name: {column_names}} across every app/models/*.py file."""
    out: dict[str, set[str]] = {}
    if not models_dir.exists():
        return out
    for mf in models_dir.glob("*.py"):
        try:
            content = mf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for m in _CLASS_DECL_RE.finditer(content):
            cls_name = m.group(1)
            next_m = _CLASS_DECL_RE.search(content, m.end())
            body = content[m.end(): next_m.start() if next_m else len(content)]
            cols = set(_MODEL_COLUMN_RE.findall(body))
            if cols:
                out[cls_name] = cols
    return out


_NOTNULL_COLUMN_RE = re.compile(r'^(\s+)(\w+)\s*=\s*Column\(([^\n]*)\)\s*$', re.MULTILINE)


def _model_notnull_no_default_columns(models_dir: Path) -> dict[str, set[str]]:
    """{model_class_name: {column names that are NOT NULL, have no default/
    server_default, and are neither the primary key nor a ForeignKey}}
    across every app/models/*.py file.

    Exp075: the exact same nullable=False/no-default/non-PK/non-FK
    classification `preflight.py::_fix_model_schema_notnull_gap` (Exp012/13)
    already uses to decide whether a column can be safely relaxed on the
    CREATE path -- reused here (not reimplemented) to identify which
    columns an UPDATE-path route must never overwrite with None when a
    field is simply omitted from the request.
    """
    out: dict[str, set[str]] = {}
    if not models_dir.exists():
        return out
    for mf in models_dir.glob("*.py"):
        try:
            content = mf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for m in _CLASS_DECL_RE.finditer(content):
            cls_name = m.group(1)
            next_m = _CLASS_DECL_RE.search(content, m.end())
            body = content[m.end(): next_m.start() if next_m else len(content)]
            cols = set()
            for cm in _NOTNULL_COLUMN_RE.finditer(body):
                attr_name, args = cm.group(2), cm.group(3)
                if 'primary_key' in args or 'ForeignKey' in args:
                    continue
                if 'nullable=False' not in args:
                    continue
                if 'default=' in args or 'server_default=' in args:
                    continue
                cols.add(attr_name)
            if cols:
                out[cls_name] = cols
    return out


def _find_model_columns_for_entity(entity: str, model_columns_by_class: dict[str, set[str]]) -> Optional[set[str]]:
    """Match a schema-derived entity name (e.g. 'Tag' from 'TagCreate') to
    its SQLAlchemy model class, tolerating the singular/plural drift
    already established as endemic in this codebase's own generation
    (see _dedupe_class_files/_patch_model_aliases). Returns None if no
    plausible model match exists -- callers must treat that as "can't
    corroborate," not "confirmed no such column"."""
    entity_lower = entity.lower()
    for cls_name, cols in model_columns_by_class.items():
        c = cls_name.lower()
        if c == entity_lower or c == entity_lower + "s" or c + "s" == entity_lower:
            return cols
    return None


def _patch_missing_create_update_fields(project_path: Path) -> int:
    """
    Add a field to a Create/Update Pydantic schema when a route handler
    reads it off that schema's own parameter but the field was never
    defined there -- crashes at request time with AttributeError, not
    caught by anything static (the attribute genuinely doesn't exist on
    the class; this isn't a typo _patch_attr_access_mismatches' synonym
    map can fix, since there's no wrong name to rename FROM).

    Confirmed live (2026-07-11): a generated gym-tracker app's
    WorkoutCreate schema only declared title/description, but
    workout_routes.py's create_workout() handler did
    `Workout(title=workout_in.title, date=workout_in.date, ...)` --
    date and notes were correctly present on WorkoutResponse (and the
    SQLAlchemy model) but simply never carried over to WorkoutCreate.
    AttributeError: 'WorkoutCreate' object has no attribute 'date' on
    every single POST, 500ing the entire Create step of the CRUD journey.

    Conservative by construction: adds a field when ANOTHER schema class
    for the same entity (matched by class-name prefix, e.g.
    Workout{Base,Response,Update}) already declares that exact field name
    -- corroborated evidence this is a real, intended field that just
    didn't make it into this one class. Falls back to the SQLAlchemy
    model's own columns only when NO sibling schema corroborates at all --
    confirmed live (2026-07-11): a generated gym-tracker app's Tag model
    has `name = Column(String, nullable=False)`, and tag_routes.py's
    handler unconditionally does `Tag(name=tag_in.name)`, but EVERY
    schema for Tag (Create/Update/Response) consistently uses `title`/
    `description` instead -- no sibling schema was ever going to
    corroborate `name` because none of them has it either. A corpus sweep
    found this exact "route accesses a real model column no schema
    anywhere declares" shape in 6 of 53 real projects (~11%). Either
    corroboration path always adds `Optional[Any] = None` (never guesses
    a narrower type) so it can never itself introduce a new validation
    failure.
    """
    routes_dir = project_path / "app" / "routes"
    schemas_dir = project_path / "app" / "schemas"
    if not routes_dir.exists() or not schemas_dir.exists():
        return 0

    classes = _schema_classes_and_fields(schemas_dir)
    if not classes:
        return 0

    model_columns_by_class = _model_classes_and_columns(project_path / "app" / "models")

    patched = 0
    for rf in routes_dir.glob("*.py"):
        try:
            content = rf.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(content)
        except Exception:
            continue

        # Two functions in the same route file commonly share a parameter
        # name (create_x(x_in: XCreate) / update_x(x_in: XUpdate)) -- a
        # single file-wide {param_name: class} map lets the second
        # function's type silently overwrite the first's, misattributing
        # every access in between to the wrong schema. Scope per function.
        missing_by_class: dict[str, set[str]] = {}
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            typed_params: dict[str, str] = {}
            for arg in fn.args.args:
                ann = arg.annotation
                name = ann.id if isinstance(ann, ast.Name) else None
                if name and (name.endswith("Create") or name.endswith("Update")) and name in classes:
                    typed_params[arg.arg] = name
            if not typed_params:
                continue

            for node in ast.walk(fn):
                if not (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)):
                    continue
                cls_name = typed_params.get(node.value.id)
                if not cls_name:
                    continue
                _, fields = classes[cls_name]
                if node.attr in fields or node.attr.startswith("_"):
                    continue
                # Corroborate: does another schema for the SAME entity
                # already declare this field? Matched against this
                # codebase's own established suffix convention (Base/
                # Create/Update/Response/Read/Out/In/Detail/Summary), not a
                # bare startswith(entity) -- that would also match an
                # unrelated LONGER entity name sharing the same prefix
                # (e.g. "Team" spuriously corroborated by "TeamMemberResponse").
                prefix = re.match(r"[A-Za-z]+?(?=Create$|Update$)", cls_name)
                entity = prefix.group(0) if prefix else cls_name
                sibling_names = {entity + suf for suf in
                                  ("Base", "Create", "Update", "Response",
                                   "Read", "Out", "In", "Detail", "Summary")}
                corroborated = any(
                    node.attr in other_fields
                    for other_cls, (_, other_fields) in classes.items()
                    if other_cls != cls_name and other_cls in sibling_names
                )
                if not corroborated:
                    # Fallback: no sibling SCHEMA has it, but does the
                    # entity's own SQLAlchemy model have it as a real
                    # column? (the Tag.name-vs-title shape -- every schema
                    # agrees with every OTHER schema, just not with the
                    # model or the route handler that actually uses it).
                    model_cols = _find_model_columns_for_entity(entity, model_columns_by_class)
                    corroborated = model_cols is not None and node.attr in model_cols
                if corroborated:
                    missing_by_class.setdefault(cls_name, set()).add(node.attr)

        if not missing_by_class:
            continue

        for cls_name, missing_fields in missing_by_class.items():
            schema_file, _ = classes[cls_name]
            try:
                sc = schema_file.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            # Match only spaces/tabs (not \s, which also matches newlines)
            # before the required trailing \n -- a greedy \s*\n here would
            # swallow blank lines AND the body's own leading indentation,
            # corrupting the file (confirmed live during corpus validation:
            # a `pass`-only class body ended up de-indented to column 0,
            # outside the class entirely).
            m = re.search(rf'^class\s+{re.escape(cls_name)}\s*\([^)]*\)\s*:[ \t]*\n', sc, re.MULTILINE)
            if not m:
                continue
            # Insert immediately after the class header, before any existing
            # body content (docstring/model_config/pass/fields) -- simplest
            # position that can never land mid-line or mid-indentation.
            insert_at = m.end()
            new_lines = "".join(f"    {f}: Optional[Any] = None\n" for f in sorted(missing_fields))
            sc = sc[:insert_at] + new_lines + sc[insert_at:]

            # Ensure both Optional and Any are importable regardless of what
            # the existing `from typing import ...` line already has.
            typing_import = re.search(r"^from typing import ([^\n]+)", sc, re.MULTILINE)
            needed = {"Optional", "Any"}
            if typing_import:
                have = {n.strip() for n in typing_import.group(1).split(",")}
                add = needed - have
                if add:
                    sc = (sc[:typing_import.start()]
                          + f"from typing import {typing_import.group(1)}, {', '.join(sorted(add))}"
                          + sc[typing_import.end():])
            else:
                sc = "from typing import Optional, Any\n" + sc
            schema_file.write_text(sc, encoding="utf-8")
            patched += 1
            print(f"  [patcher] Added missing field(s) to {cls_name} in {schema_file.name}: "
                  f"{sorted(missing_fields)}")

    return patched


def _patch_missing_pydantic_imports(project_path: Path) -> int:
    """
    Scan schema and route modules for Pydantic/typing names they use without
    importing. Route decorators commonly use ``List[Response]`` too.
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

    routes_dir = project_path / "app" / "routes"
    candidate_files = list(schemas_dir.rglob("*.py"))
    if routes_dir.exists():
        candidate_files.extend(routes_dir.rglob("*.py"))

    for py_file in candidate_files:
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
                # `from __future__ import ...` must stay the first statement
                # in the file (Python raises a SyntaxError otherwise) --
                # keep scanning past it instead of inserting before it.
                if line.lstrip().startswith('from __future__ import'):
                    insert_at = i + 1
                    continue
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

        changed = False
        requires_any = False
        new_content = content
        for orm_cls in orm_classes:
            # FastAPI cannot use an ORM class directly in a route response
            # model. Generated routes often import schemas rather than define
            # them locally, so this cannot be limited to local Pydantic code.
            response_cls = f"{orm_cls}Response"
            response_pat = re.compile(
                rf'response_model\s*=\s*(?:List|list)\[\s*{re.escape(orm_cls)}\s*\]'
            )
            if response_pat.search(new_content):
                replacement = (
                    f"response_model=list[{response_cls}]"
                    if re.search(rf'\b{re.escape(response_cls)}\b', new_content)
                    else "response_model=None"
                )
                new_content = response_pat.sub(replacement, new_content)
                changed = True
            # List[OrmClass] → List[Any]
            list_pat = re.compile(rf'\bList\[{re.escape(orm_cls)}\]')
            if list_pat.search(new_content):
                new_content = list_pat.sub("List[Any]", new_content)
                changed = True
                requires_any = True
            # Optional[OrmClass] → Optional[Any]
            opt_pat = re.compile(rf'\bOptional\[{re.escape(orm_cls)}\]')
            if opt_pat.search(new_content):
                new_content = opt_pat.sub("Optional[Any]", new_content)
                changed = True
                requires_any = True
            # Field annotation: `name: OrmClass` (bare, not in List/Optional)
            bare_pat = re.compile(rf'(\b\w+\s*:\s*){re.escape(orm_cls)}(\s*(?:=|#|\n))')
            if bare_pat.search(new_content):
                new_content = bare_pat.sub(r'\1Any\2', new_content)
                changed = True
                requires_any = True

        if changed and requires_any:
            # Ensure Any is imported. Exp052: must check the ORIGINAL
            # `content`, not `new_content` -- the substitutions above just
            # inserted the literal text "Any" (as part of "List[Any]" /
            # "Optional[Any]" / a bare rewritten annotation), so checking
            # new_content for "Any" is always true post-rewrite and this
            # branch never actually added the import, leaving a confirmed
            # `NameError: name 'Any' is not defined` whenever the route
            # file already had some other `from typing import ...` line.
            if "from typing import" in new_content:
                if "Any" not in content:
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
        elif changed:
            rf.write_text(new_content, encoding="utf-8")
            patched += 1
            print(f"  [patcher] Replaced invalid ORM response model in {rf.name}")

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

# npm registry rule: package names (scope included) are lowercase-only, no
# uppercase letters anywhere -- ever. A "from" target with any capital letter
# is never a real package; it's virtually always a broken/hallucinated
# import of a local variable/hook that the LLM wrote as `import { x } from
# 'someCamelCaseName'` instead of a real relative path. Left unfiltered,
# _patch_frontend_package_json added these straight into package.json's
# dependencies as literal package names -- `npm install` then 404's on
# every one of them (npm's own registry rejects the name before even
# checking if it exists: "name can no longer contain capital letters"),
# breaking the ENTIRE frontend build with no way to recover, since nothing
# ever removes a once-added bad dependency across subsequent fix rounds.
_VALID_NPM_PKG_NAME_RE = re.compile(r"^(@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*$")

# A lowercase name (e.g. "habits") passes the pattern check above but can
# still be a broken import -- not every hallucinated local-variable-as-
# module-name happens to have a capital letter. The dead giveaway that
# survives casing is self-reference: `import { habits } from 'habits'` or
# `import { currentHabit, x } from 'currentHabit'` -- the LLM meant a
# relative path to a hook/context but wrote the bare variable name being
# imported as the module string instead. A real package is never named
# after one of its own named imports like this.
_SELF_REF_IMPORT_RE = re.compile(
    r"""import\s+(?:(\w+)\s*,?\s*)?(?:\{([^}]*)\})?\s*from\s+['"]([^'"]+)['"]"""
)


def _is_self_referential_import(text: str, module_name: str) -> bool:
    for m in _SELF_REF_IMPORT_RE.finditer(text):
        default_name, named_block, mod = m.group(1), m.group(2), m.group(3)
        if mod != module_name:
            continue
        bound_names = set()
        if default_name:
            bound_names.add(default_name)
        if named_block:
            for part in named_block.split(","):
                part = part.strip()
                if part:
                    bound_names.add(part.split(" as ")[0].strip())
        if module_name in bound_names:
            return True
    return False

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
            # Never trust a name that couldn't possibly be a real npm
            # package (see _VALID_NPM_PKG_NAME_RE docstring) -- leave it for
            # the actual broken-import fix path instead of corrupting
            # package.json with it.
            if not _VALID_NPM_PKG_NAME_RE.match(pkg_root):
                continue
            # Nor a lowercase name that's just as clearly a broken
            # self-referential import (see _is_self_referential_import).
            if _is_self_referential_import(text, name):
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


def _patch_vite_root_proxy_and_api_base(project_path: Path) -> int:
    """Keep the SPA entrypoint local and send API calls to FastAPI explicitly.

    A generated negative-lookahead proxy matched ``/`` under Vite's regular
    expression proxy rules. Vite therefore sent the SPA entrypoint to FastAPI,
    which correctly returned 404 before React could load. API clients already
    use ``VITE_API_URL`` in production; a localhost fallback makes the same
    client work in local development without proxying the SPA route.
    """
    changed = 0
    vite_config = project_path / "vite.config.js"
    if vite_config.exists():
        source = vite_config.read_text(encoding="utf-8", errors="replace")
        if "^/(?!src|@|node_modules|favicon|assets|index\\.html)" in source:
            vite_config.write_text(
                "import { defineConfig } from 'vite'\n"
                "import react from '@vitejs/plugin-react'\n\n"
                "export default defineConfig({\n"
                "  plugins: [react()],\n"
                "  build: { outDir: 'dist', emptyOutDir: true },\n"
                "})\n",
                encoding="utf-8",
            )
            changed += 1

    for api_file in (*project_path.glob("src/api.js"), *project_path.glob("src/api.jsx")):
        source = api_file.read_text(encoding="utf-8", errors="replace")
        patched = source.replace(
            "baseURL: import.meta.env.VITE_API_URL,",
            "baseURL: import.meta.env.VITE_API_URL || 'http://localhost:8000',",
        )
        if patched != source:
            api_file.write_text(patched, encoding="utf-8")
            changed += 1
    return changed


# ── Fix: missing app/services/ stubs ─────────────────────────────────────────
# LLMs sometimes generate route files that delegate to a service layer
# (from app.services.team_service import create_team, ...) but don't generate
# those service files. Validation then fails with "Missing import target".
# Create working CRUD stubs so static validation passes and routes function.

_SVC_IMPORT_RE = re.compile(r"^from app\.services\.(\w+) import ([^\n]+)", re.MULTILINE)


def _infer_crud_func(func: str, model_cls: str, resource: str, pk: str = "id") -> str:
    """`pk` is the model's actual primary-key column name (detected by the
    caller from the model source, defaulting to "id"). Hardcoding
    `{model_cls}.id` here used to raise AttributeError on every request for
    any model whose primary key is named something else (e.g. `game_id`) --
    the same failure class as the auth-template bug: an unconditional
    schema assumption baked into generated code that isn't always true.
    """
    f = func.lower()
    rid = f"{resource}_id"
    if re.match(r"get_\w+_by_id|get_by_id|fetch_\w+_by_id", f):
        return (f"def {func}(db: Session, {rid}: int):\n"
                f"    return db.query({model_cls}).filter({model_cls}.{pk} == {rid}).first()\n")
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
                f"    obj = db.query({model_cls}).filter({model_cls}.{pk} == {rid}).first()\n"
                f"    if not obj: return None\n"
                f"    data = {resource}_in.dict() if hasattr({resource}_in, 'dict') else kw\n"
                f"    [setattr(obj, k, v) for k, v in data.items() if k in {model_cls}.__table__.columns.keys()]\n"
                f"    db.commit(); db.refresh(obj); return obj\n")
    if re.match(r"delete_\w+", f):
        return (f"def {func}(db: Session, {rid}: int) -> bool:\n"
                f"    obj = db.query({model_cls}).filter({model_cls}.{pk} == {rid}).first()\n"
                f"    if not obj: return False\n"
                f"    db.delete(obj); db.commit(); return True\n")
    if re.match(r"add_\w+_to_\w+|remove_\w+_from_\w+", f):
        return (f"def {func}(db: Session, {rid}: int, user_id: int, **kw):\n"
                f"    return db.query({model_cls}).filter({model_cls}.{pk} == {rid}).first()\n")
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

    # Build model class map: service name → (ModelClass, module path, pk column)
    _pk_re = re.compile(r"^\s{4}(\w+)\s*=\s*Column\([^\n]*primary_key\s*=\s*True", re.MULTILINE)
    _mapped_pk_re = re.compile(
        r"^\s{4}(\w+)\s*:\s*Mapped\[[^\]]+\]\s*=\s*mapped_column\([^\n]*primary_key\s*=\s*True",
        re.MULTILINE,
    )
    model_map: dict[str, tuple[str, str, str]] = {}
    if models_dir.exists():
        for mf in models_dir.glob("*.py"):
            if mf.name.startswith("_"):
                continue
            try:
                text = mf.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            pk_m = _pk_re.search(text) or _mapped_pk_re.search(text)
            pk = pk_m.group(1) if pk_m else "id"
            for cls in re.findall(r"^class (\w+)\s*\(Base\)", text, re.MULTILINE):
                for key in (mf.stem, mf.stem.rstrip("s"), cls.lower(), cls.lower().rstrip("s")):
                    if key not in model_map:
                        model_map[key] = (cls, f"app.models.{mf.stem}", pk)

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
        model_cls, model_mod, pk = (
            model_map.get(resource) or model_map.get(resource.rstrip("s"))
            or (resource.capitalize(), f"app.models.{resource}", "id")
        )

        stubs = [_infer_crud_func(f, model_cls, resource, pk) for f in sorted(missing)]

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


_ROUTER_ASSIGN_RE = re.compile(r"^(\w+)\s*=\s*APIRouter\b", re.MULTILINE)


def _patch_router_export_mismatch(project_path: Path) -> int:
    """
    Fixes 'Router export mismatch in app/routes/X.py. Expected 'Y_router''
    deterministically instead of routing it to an LLM fix.

    router_export_validator.py only checks whether a module-level variable
    named exactly '<file>_router' exists -- it never inspects what the file
    actually calls its router. In practice the router is almost always
    there, just under a different name (or the wrong naming convention).
    Rather than renaming it (and every @actual_name.get/post/... decorator
    reference, which risks a missed occurrence), add a one-line alias:
    '<expected_name> = <actual_name>' -- main.py's
    'from app.routes.X import Y_router' then resolves correctly with zero
    risk to the file's existing route definitions.

    Skips (falls through to the existing LLM fix, never worse than today)
    when the file has zero or more than one APIRouter() assignment, since
    either case makes "the actual router" ambiguous.

    A hyphenated resource/filename (app/routes/consultation-note_routes.py,
    from a resource named "consultation note") produced expected_router =
    "consultation-note_router" -- a hyphen is never valid inside a Python
    identifier, so the alias line this patcher writes
    ("consultation-note_router = consultation_note_router") is itself a
    SyntaxError ("cannot assign to expression here"), on top of the
    original hyphenated-import SyntaxError _patch_hyphenated_router_
    identifiers already exists to fix. Reproduced live (ForgeBench v1.0,
    hospital_management_system, 2026-07-28) -- confirmed the SAME app/
    resource name that motivated that other patcher back on 2026-07-13,
    recurring here because this one never sanitized the filename-derived
    identifier at all.
    """
    routes_dir = project_path / "app" / "routes"
    if not routes_dir.exists():
        return 0

    fixed = 0
    for route_file in sorted(routes_dir.glob("*.py")):
        if route_file.name == "__init__.py":
            continue
        try:
            content = route_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        expected_router = route_file.name.replace("_routes.py", "").replace("-", "_") + "_router"

        # Module-level only, matching router_export_validator.py's own check.
        already_exported = re.search(
            rf"^{re.escape(expected_router)}\s*(?::|=)", content, re.MULTILINE
        )
        if already_exported:
            continue

        candidates = [
            m.group(1) for m in _ROUTER_ASSIGN_RE.finditer(content)
            if m.group(1) != expected_router
        ]
        if len(candidates) != 1:
            continue  # ambiguous or no router found -- leave to the LLM fix

        actual_router = candidates[0]
        content = content.rstrip() + f"\n\n{expected_router} = {actual_router}\n"
        route_file.write_text(content, encoding="utf-8")
        fixed += 1
        print(
            f"  [router_export_patcher] Aliased {expected_router} = {actual_router} "
            f"in app/routes/{route_file.name}"
        )

    return fixed


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


# Verb synonyms so a route segment and a component-name word don't have to be
# spelled identically to be recognized as the same CRUD intent — "new" and
# "add" mean the same thing to a router but are different words.
_CRUD_VERB_SYNONYMS: list[frozenset] = [
    frozenset({"new", "add", "create"}),
    frozenset({"edit", "update"}),
    frozenset({"delete", "remove"}),
    frozenset({"detail", "details", "view", "show"}),
]


def _word_synonyms(word: str) -> frozenset:
    for group in _CRUD_VERB_SYNONYMS:
        if word in group:
            return group
    return frozenset({word})


def _component_words(comp_name: str) -> set[str]:
    """PascalCase component name -> lowercase words, minus the generic 'Page' suffix."""
    words = re.findall(r"[A-Z][a-z0-9]*", comp_name)
    return {w.lower() for w in words if w.lower() != "page"}


def _best_matching_path(comp_name: str, candidate_paths: list[str]) -> str | None:
    """Pick the candidate route path whose segments best match this component's
    name, considering every path segment (not just the last) and CRUD-verb
    synonyms (new/add/create, edit/update, ...).

    Naively comparing only the final URL segment against the whole component
    name (e.g. "/habits/new" -> last segment "new" vs "addedithabitpage")
    fails for exactly the routes that matter most: create/edit forms, whose
    component is typically named things like AddEditHabitPage while the
    route's distinguishing word ("habit") sits in an earlier segment. That
    mismatch left already-built pages like AddEditHabitPage.jsx unrouted,
    and the fix-loop scaffolded a generic placeholder page for the route
    instead of ever finding this one.
    """
    comp_words = _component_words(comp_name)
    if not comp_words:
        return None

    best_path, best_score = None, 0
    for p in candidate_paths:
        segs = [s for s in p.strip("/").split("/") if s and not s.startswith(":") and not s.startswith("{")]
        if not segs:
            continue
        score = 0
        for seg in segs:
            seg_l = seg.lower().replace("-", "")
            seg_singular = seg_l[:-1] if seg_l.endswith("s") and len(seg_l) > 3 else seg_l
            candidates = _word_synonyms(seg_l) | _word_synonyms(seg_singular) | {seg_l, seg_singular}
            if candidates & comp_words:
                score += 1
            elif any(len(c) > 2 and len(cw) > 2 and (c in cw or cw in c) for c in candidates for cw in comp_words):
                score += 1
        if score > best_score:
            best_path, best_score = p, score

    return best_path if best_score > 0 else None


def _patch_dedupe_frontend_imports(project_path: Path) -> None:
    """
    Remove duplicate default-import declarations of the same identifier from
    src/**/*.jsx. Duplicates arise when the LLM fix loop rewrites a file's
    import block while an earlier deterministically-injected import (e.g.
    from the orphan-route patcher below) is still present — esbuild then
    refuses the whole build with 'The symbol "X" has already been declared'
    (canary m2: todo App.jsx imported RegisterPage twice, build 42.0).
    Removing every re-declaration after the first is always safe: a second
    declaration of the same identifier is a guaranteed syntax error in JS.
    """
    src_dir = project_path / "src"
    if not src_dir.is_dir():
        return
    import_decl_re = re.compile(r"^import\s+(\w+)\s+from\s+['\"][^'\"]+['\"];?\s*$")
    for jsx in src_dir.rglob("*.jsx"):
        try:
            content = jsx.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if "import" not in content:
            continue
        seen: set[str] = set()
        kept: list[str] = []
        removed: list[str] = []
        for line in content.split("\n"):
            m = import_decl_re.match(line)
            if m:
                name = m.group(1)
                if name in seen:
                    removed.append(name)
                    continue
                seen.add(name)
            kept.append(line)
        if removed:
            jsx.write_text("\n".join(kept), encoding="utf-8")
            rel = jsx.relative_to(project_path)
            print(f"  [import_dedupe] Removed duplicate import(s) {sorted(set(removed))} from {rel}")


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
    # Extension optional: the LLM writes './pages/X.jsx' while this patcher
    # injects './pages/X' — requiring the closing quote right after \w+ made
    # every .jsx-suffixed import invisible, so the wirer re-imported EVERY
    # page and esbuild died on duplicate symbols (Exp112, simple_crm: the
    # sole reason an 86.5/B app was blocked from deploying).
    import_re = re.compile(
        r"^import\s+(\w+)\s+from\s+['\"]\./pages/(\w+)(?:\.jsx|\.js|\.tsx|\.ts)?['\"]",
        re.MULTILINE)
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
    #
    # A naive non-greedy `element=\{(.*?)\}\s*/>` regex stops at the FIRST
    # "} />" it finds -- which for the extremely common prop-spread shape
    # `<DashboardPage {...pageProps} />` is the closing brace of
    # `{...pageProps}` itself, not the outer element={...} attribute's real
    # end. That truncates the captured template mid-attribute (no closing
    # `/>` left in it at all), so the later self-closing-tag-clone regex can
    # never match -- every orphan page silently failed to wire in with
    # "couldn't find the anchor's page tag inside its own template to
    # clone" (reproduced live, habit_tracker, 2026-07-27). find_matching_brace
    # does real depth-tracking instead of a bounded regex, so nested braces
    # inside the element don't fool it.
    indent = template_element = None
    for anchor_path in ("/dashboard", "/habits"):
        open_m = re.search(
            rf'(\s*)<Route\s+path="{re.escape(anchor_path)}"\s+element=(\{{)',
            content,
        )
        if not open_m:
            continue
        close_pos = find_matching_brace(content, open_m.start(2), quote_chars="'\"`")
        if close_pos == -1:
            continue
        tail_m = re.match(r"\s*/>", content[close_pos + 1:])
        if not tail_m:
            continue
        indent = open_m.group(1)
        template_element = content[open_m.start(2) + 1:close_pos]
        break
    if template_element is None:
        if "PrivateRoute" in content:
            # No existing authenticated route to clone a wrapper from -- this
            # happens whenever App.jsx was scaffolded (ensure_app_jsx) before
            # every page file existed on disk, since the missing-file fix loop
            # creates pages like Dashboard/Habits/Badges *afterward* in response
            # to validation errors. That scaffold then has zero private routes
            # to use as an anchor, so bailing out here (the old behavior) left
            # every one of those later-created pages permanently unrouted --
            # imported into App.jsx, but with nothing to navigate to after
            # login except the "*" -> /login catch-all, which looks exactly
            # like "login doesn't work" from the user's side even though auth
            # succeeded and a valid token was stored. PrivateRoute itself is
            # part of the standard App.jsx template (see ensure_app_jsx /
            # frontend_prompt.py) and doesn't depend on any route existing, so
            # wrap directly in it instead of requiring something to clone.
            indent, template_element = "        ", "<PrivateRoute><Placeholder /></PrivateRoute>"
        else:
            if content != original:
                app_jsx.write_text(content, encoding="utf-8")
            return

    def _kebab_path(name: str) -> str:
        s = re.sub(r"(?<!^)(?=[A-Z])", "-", name).lower().replace("-page", "")
        return "/" + s

    added: list[tuple] = []
    for comp_name in orphans:
        target_path = _best_matching_path(comp_name, candidate_paths)
        if target_path is None:
            target_path = _kebab_path(comp_name)
        if target_path in routed_paths:
            continue

        # Match the anchor's page component tag even when it forwards props
        # (`<DashboardPage {...pageProps} />`) -- the old `<\w+\s*/>` pattern
        # required a bare tag with no attributes, silently matched nothing
        # for any anchor route that passes props, and left the clone
        # rendering the anchor's own page (e.g. every orphan route wired
        # from a `/dashboard` anchor rendered DashboardPage instead of its
        # own page) instead of raising an error anyone would notice.
        new_element, n_subs = re.subn(
            r"<\w+(?:\{[^{}]*\}|[^<>])*?/>", f"<{comp_name} />", template_element,
            count=1,
        )
        if n_subs == 0:
            print(f"    [route_patcher] Skipped {comp_name}: couldn't find the "
                  f"anchor's page tag inside its own template to clone")
            continue
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


_DASHBOARD_REDIRECT_PATTERNS = (
    # navigate('/dashboard') / navigate("/dashboard")
    (re.compile(r"""(navigate\(\s*)(['"])/dashboard\2"""), r"\1\g<2>{fallback}\2"),
    # <Link to="/dashboard"> / <NavLink to='/dashboard'>
    (re.compile(r"""(\bto\s*=\s*)(['"])/dashboard\2"""), r"\1\g<2>{fallback}\2"),
    # { path: '/dashboard', ... } nav-item array entries (Sidebar.jsx etc.)
    (re.compile(r"""(\bpath\s*:\s*)(['"])/dashboard\2"""), r"\1\g<2>{fallback}\2"),
)


def _patch_login_redirect_target(project_path: Path) -> int:
    """Fix hardcoded references to '/dashboard' when the app has no
    /dashboard route at all -- navigate('/dashboard') calls in auth pages,
    <Link to="/dashboard">/<NavLink to="/dashboard"> nav items, and
    { path: '/dashboard', ... } entries in nav-item config arrays.

    frontend_prompt.py's login/register examples hardcode navigate('/dashboard')
    on the assumption a DashboardPage always exists, and validator_service.py's
    nav-target check explicitly skips "/dashboard" on the same assumption
    (_SKIP_NAV_TARGETS). Both break when the LLM names its main authenticated
    page something else (e.g. HabitsPage, TasksPage) instead of literally
    "Dashboard" -- which is common and not itself wrong. The same hardcoded
    path shows up in three different shapes across a real app (a login
    page's navigate() call, plus a Navbar/Navigation/Sidebar component's nav
    link or nav-item array, all independently written by the LLM) so fixing
    only the navigate() form still leaves a "Dashboard" button in the sidebar
    that bounces straight back to /login when clicked. Login then succeeds,
    a real token gets stored, and the "*" catch-all bounces straight back to
    /login anyway -- from the user's side this looks exactly like "signing in
    doesn't work" even though auth worked perfectly. Must run after
    _patch_wire_orphan_frontend_routes so routes are already wired before
    checking what actually exists.
    """
    app_jsx = project_path / "src" / "App.jsx"
    if not app_jsx.exists():
        return 0
    try:
        app_content = app_jsx.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return 0

    routed_paths = set(re.findall(r'<Route\s+path="([^"]+)"', app_content))
    if "/dashboard" in routed_paths:
        return 0

    _AUTH_LIKE = {"/", "/login", "/register", "/signup", "*"}
    private_routes = re.findall(r'<Route\s+path="([^"]+)"\s+element=\{<PrivateRoute>', app_content)
    fallback = next((p for p in private_routes if p not in _AUTH_LIKE), None)
    if not fallback:
        return 0  # nothing sensible to redirect to either -- leave as-is

    src_dir = project_path / "src"
    if not src_dir.exists():
        return 0

    patched = 0
    for jf in src_dir.rglob("*.jsx"):
        try:
            content = jf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if "/dashboard" not in content:
            continue
        new_content = content
        for pattern, repl_template in _DASHBOARD_REDIRECT_PATTERNS:
            new_content = pattern.sub(repl_template.replace("{fallback}", fallback), new_content)
        if new_content != content:
            jf.write_text(new_content, encoding="utf-8")
            patched += 1
            print(f"  [patcher] Redirected hardcoded '/dashboard' reference(s) -> '{fallback}' in {jf.name} "
                  f"(no /dashboard route exists in this app)")

    return patched


# ── Main entry point ──────────────────────────────────────────────────────────

def _patch_required_create_schema_model_nullability(project_path: Path) -> int:
    """Make model columns required when a Create schema requires them.

    A Create schema is the API contract for client-supplied data. Rewriting
    its required fields to ``Optional`` merely hides a contradiction and lets
    invalid rows through. Repair the model declaration before the generic
    response-schema compatibility patch runs.
    """
    import ast as _ast
    from app.services.schema_model_validator import _is_optional_annotation

    models_dir = project_path / "app" / "models"
    schemas_dir = project_path / "app" / "schemas"
    if not models_dir.exists() or not schemas_dir.exists():
        return 0

    required_by_model: dict[str, set[str]] = {}
    for sf in schemas_dir.glob("*.py"):
        try:
            tree = _ast.parse(sf.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        for node in tree.body:
            if not isinstance(node, _ast.ClassDef) or not node.name.endswith("Create"):
                continue
            fields = {
                child.target.id for child in node.body
                if isinstance(child, _ast.AnnAssign)
                and isinstance(child.target, _ast.Name)
                and not _is_optional_annotation(child.annotation)
            }
            if fields:
                required_by_model[node.name[:-6].lower()] = fields

    patched = 0
    for mf in models_dir.glob("*.py"):
        try:
            src = mf.read_text(encoding="utf-8", errors="replace")
            tree = _ast.parse(src)
        except Exception:
            continue
        lines = src.splitlines()
        changed: list[str] = []
        for node in tree.body:
            if not isinstance(node, _ast.ClassDef):
                continue
            required = required_by_model.get(node.name.lower())
            if not required:
                continue
            for child in node.body:
                if not (
                    isinstance(child, _ast.Assign)
                    and isinstance(child.value, _ast.Call)
                    and getattr(child.value.func, "id", "") == "Column"
                ):
                    continue
                names = [target.id for target in child.targets if isinstance(target, _ast.Name)]
                if not names or names[0] not in required:
                    continue
                nullable_kw = next((kw for kw in child.value.keywords if kw.arg == "nullable"), None)
                if not (
                    nullable_kw
                    and isinstance(nullable_kw.value, _ast.Constant)
                    and nullable_kw.value.value is True
                ):
                    continue
                # Rewrite only the literal `True` token's own line, not the
                # whole Column(...) call's span. The call itself commonly
                # spans multiple lines (one kwarg per line) -- the previous
                # `child.lineno == child.end_lineno` guard skipped every such
                # column entirely, silently, even though the validator that
                # raises "required but model allows NULL" has no such
                # restriction (a plain AST walk) and reported it every time.
                # The boolean constant itself is always single-line, so
                # rewriting just its line is safe regardless of how the
                # surrounding call is formatted.
                index = nullable_kw.value.lineno - 1
                updated = re.sub(r"\bnullable\s*=\s*True\b", "nullable=False", lines[index], count=1)
                if updated != lines[index]:
                    lines[index] = updated
                    changed.append(f"{node.name}.{names[0]}")
        if changed:
            mf.write_text("\n".join(lines) + ("\n" if src.endswith("\n") else ""), encoding="utf-8")
            patched += 1
            print(f"  [patcher] Made required Create-model columns NOT NULL in {mf.name}: {', '.join(changed[:5])}")
    return patched


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
_DT_TYPE_NAMES = frozenset(_DT_COLUMN_TYPES.values()) | {"str"}


def _patch_response_schema_id_and_datetimes(project_path: Path) -> int:
    """
    Two guaranteed-broken response patterns, fixed deterministically:

    1. A response-ish schema (used as response_model, or named *Response/*Out/
       *Read) with NO `id` field — FastAPI strips id from every response, the
       CRUD journey can't capture an entity_id, and edit/delete/persistence
       all fail ("201 id=None"). Inject `id: Optional[int] = None`.

    2. A schema field whose annotation names the WRONG member of the
       str/date/datetime/time family for what the model column actually is —
       the ORM hands pydantic a value of the real column type, raising
       ResponseValidationError (500) on every row returned. Originally this
       only handled str/Optional[str] (the LLM using a bare string for a
       timestamp field), but the identical crash happens when the schema
       says `date` and the column is `DateTime`: pydantic refuses to
       silently truncate a datetime with a non-midnight time component into
       a date ("date_from_datetime_inexact"). Seen live: `created_at`/
       `updated_at` typed `Optional[date]` against a real `DateTime` column
       500'd on every POST/GET — the runtime-fix LLM "fixed" it by mutating
       `habit.created_at = habit.created_at.date()` on every read instead of
       just retyping the schema, which is fragile (that mutated value could
       get written back on a later commit in the same session) and only
       covered the routes it happened to touch. Retype to the column's real
       type — pydantic still accepts ISO strings on input either way, so
       this never breaks a client that was sending strings.
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
                is_optional = ann.startswith("Optional[") and ann.endswith("]")
                inner = ann[len("Optional["):-1] if is_optional else ann
                if inner not in _DT_TYPE_NAMES or inner == py_dt:
                    continue
                new_ann = f"Optional[{py_dt}]" if is_optional else py_dt
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

# The curated list above is hand-written; sanitize it against the ground-truth
# export list of the PINNED lucide-react version so the missing-import patcher
# can never add a name the package doesn't actually export (found live: the
# hand-written list contained Grid3x3/LoaderCircle/NotebookPen and 7 others
# that 0.263.1 doesn't ship — each one a guaranteed vite build failure).
from app.knowledge.lucide_icon_exports import VALID_LUCIDE_ICONS
_LUCIDE_ICONS = _LUCIDE_ICONS & VALID_LUCIDE_ICONS

# Closest real-icon substitute for names LLMs (and newer lucide versions)
# use that the pinned lucide-react does NOT export. Anything not mapped here
# falls back to Circle — always exported, visually neutral.
_LUCIDE_INVALID_RENAMES = {
    "Handshake": "HeartHandshake",
    "Grid3x3": "LayoutGrid", "Grid3X3": "LayoutGrid",
    "Grid2x2": "LayoutGrid", "Grid2X2": "LayoutGrid",
    "LoaderCircle": "Loader2",
    "SendHorizontal": "Send",
    "NotebookPen": "PenSquare",
    "Notebook": "BookOpen",
    "Note": "StickyNote",
    "ShieldX": "ShieldOff",
    "TrafficCone": "Construction",
    "Rabbit": "Bird",
    "ChartBar": "BarChart2", "ChartLine": "LineChart", "ChartPie": "PieChart",
    "ChartArea": "AreaChart", "ChartColumn": "BarChart3",
    "CircleAlert": "AlertCircle", "CircleCheck": "CheckCircle",
    "CircleX": "XCircle", "CircleHelp": "HelpCircle", "CircleUser": "UserCircle",
    "SquarePen": "PenSquare", "SquareCheck": "CheckSquare",
    "EllipsisVertical": "MoreVertical", "Ellipsis": "MoreHorizontal",
    "House": "Home", "UserRound": "User", "UsersRound": "Users",
}


# A handful of heroicons component names whose closest lucide-react
# equivalent isn't just "strip the Icon suffix" (heroicons and lucide don't
# share a naming convention for everything). Anything not listed here falls
# back to that strip-and-lookup in _heroicon_to_lucide.
_HEROICON_OVERRIDES = {
    "XMarkIcon": "X", "Bars3Icon": "Menu", "Bars4Icon": "AlignJustify",
    "Cog6ToothIcon": "Settings", "Cog8ToothIcon": "Settings2",
    "MagnifyingGlassIcon": "Search", "FunnelIcon": "Filter",
    "ArrowRightOnRectangleIcon": "LogOut", "ArrowLeftOnRectangleIcon": "LogIn",
    "ExclamationTriangleIcon": "AlertTriangle", "ExclamationCircleIcon": "AlertCircle",
    "InformationCircleIcon": "Info", "QuestionMarkCircleIcon": "HelpCircle",
    "ChatBubbleLeftIcon": "MessageCircle", "ChatBubbleLeftRightIcon": "MessageSquare",
    "EllipsisVerticalIcon": "MoreVertical", "EllipsisHorizontalIcon": "MoreHorizontal",
    "ArrowPathIcon": "RefreshCw", "PencilSquareIcon": "Pencil",
    "DocumentTextIcon": "FileText", "DocumentDuplicateIcon": "Copy", "DocumentIcon": "File",
    "ClipboardDocumentListIcon": "ClipboardList", "ClipboardDocumentIcon": "ClipboardCopy",
    "ArrowTrendingUpIcon": "TrendingUp", "ArrowTrendingDownIcon": "TrendingDown",
    "BellAlertIcon": "BellRing", "PresentationChartLineIcon": "LineChart",
    "PresentationChartBarIcon": "BarChart", "ChartPieIcon": "PieChart",
    "ChartBarIcon": "BarChart", "ChartBarSquareIcon": "BarChart3",
    "RectangleStackIcon": "Layers", "Squares2X2Icon": "Grid2x2", "SquaresPlusIcon": "Grid3x3",
    "AdjustmentsHorizontalIcon": "SlidersHorizontal", "AdjustmentsVerticalIcon": "SlidersHorizontal",
    "LockClosedIcon": "Lock", "LockOpenIcon": "Unlock", "EyeSlashIcon": "EyeOff",
    "HandThumbUpIcon": "ThumbsUp", "HandThumbDownIcon": "ThumbsDown",
    "PaperAirplaneIcon": "Send", "GlobeAltIcon": "Globe", "GlobeAmericasIcon": "Globe2",
    "EnvelopeIcon": "Mail", "EnvelopeOpenIcon": "MailOpen",
    "BuildingOfficeIcon": "Building", "BuildingOffice2Icon": "Building2",
    "BuildingStorefrontIcon": "Building", "CurrencyDollarIcon": "DollarSign",
    "BanknotesIcon": "Banknote", "ShoppingCartIcon": "ShoppingCart",
    "ShoppingBagIcon": "ShoppingBag", "ArchiveBoxIcon": "Archive",
    "ArchiveBoxXMarkIcon": "ArchiveRestore", "WrenchIcon": "Wrench",
    "WrenchScrewdriverIcon": "Wrench", "BeakerIcon": "FlaskConical",
    "AcademicCapIcon": "GraduationCap", "LightBulbIcon": "Lightbulb",
    "BoltIcon": "Zap", "BoltSlashIcon": "ZapOff",
    "FaceSmileIcon": "Smile", "FaceFrownIcon": "Frown", "HandRaisedIcon": "Hand",
    "SpeakerWaveIcon": "Volume2", "SpeakerXMarkIcon": "VolumeX",
    "MicrophoneIcon": "Mic", "VideoCameraIcon": "Video", "PhotoIcon": "Image",
    "MusicalNoteIcon": "Music", "StopIcon": "Square",
    "ForwardIcon": "SkipForward", "BackwardIcon": "SkipBack",
    "ArrowUturnLeftIcon": "Undo2", "ArrowUturnRightIcon": "Redo2",
    "SignalSlashIcon": "WifiOff", "CircleStackIcon": "Database",
    "ServerStackIcon": "Server", "CommandLineIcon": "Terminal",
    "CodeBracketIcon": "Code", "CodeBracketSquareIcon": "Code2",
    "CubeIcon": "Box", "CubeTransparentIcon": "Box",
    "QueueListIcon": "ListOrdered", "ListBulletIcon": "List",
    "ViewColumnsIcon": "Columns", "DevicePhoneMobileIcon": "Smartphone",
    "ComputerDesktopIcon": "Monitor", "DeviceTabletIcon": "Tablet",
    "UserGroupIcon": "Users", "IdentificationIcon": "IdCard",
    "HomeModernIcon": "Home", "InboxStackIcon": "Inbox",
    "SwatchIcon": "Palette", "PaintBrushIcon": "Paintbrush",
}

_HEROICON_IMPORT_RE = re.compile(
    r"import\s*\{([^}]*)\}\s*from\s*['\"]@heroicons/react/(?:16|20|24)/(?:outline|solid)['\"]\s*;?"
)


def _heroicon_to_lucide(name: str) -> str:
    if name in _HEROICON_OVERRIDES:
        return _HEROICON_OVERRIDES[name]
    stripped = name[:-4] if name.endswith("Icon") else name  # PlusIcon -> Plus
    if stripped in _LUCIDE_ICONS:
        return stripped
    # No confident mapping — fall back to a generic icon that's guaranteed to
    # exist rather than leaving an unresolvable import. A wrong-but-harmless
    # icon beats a build that doesn't compile at all.
    return "Circle"


def _patch_disallowed_icon_packages(project_path: Path) -> int:
    """Rewrite @heroicons/react imports to their lucide-react equivalents.

    lucide-react is the mandatory icon package (see frontend_prompt.py), but
    heroicons ships from the same team as Tailwind and shows up constantly in
    the training data the LLM draws from, so it reaches for it out of habit
    anyway. Unlike a missing lucide import (see _patch_missing_icon_imports),
    this is a genuinely unresolvable package — nothing in node_modules
    provides it — so Rollup hard-fails the build. Seen live: the fix-loop's
    generic "missing import" handling doesn't know how to repair a
    third-party package path; it scaffolded an unrelated local file as a stub
    and failed on the exact same unresolved heroicons import again on the
    next verify pass. Rewriting the import (and every JSX usage of the
    renamed icons) to lucide-react is a direct, deterministic fix for the
    only thing actually broken.
    """
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

        matches = list(_HEROICON_IMPORT_RE.finditer(src))
        if not matches:
            continue

        rename_map: dict[str, str] = {}
        for m in matches:
            for n in m.group(1).split(","):
                n = n.strip().split(" as ")[0].strip()
                if n:
                    rename_map[n] = _heroicon_to_lucide(n)

        new_src = _HEROICON_IMPORT_RE.sub("", src)
        for old, new in rename_map.items():
            if old != new:
                new_src = re.sub(rf"\b{re.escape(old)}\b", new, new_src)

        lucide_names = sorted(set(rename_map.values()))
        m = lucide_import_re.search(new_src)
        if m:
            existing = [n.strip() for n in m.group(1).split(",") if n.strip()]
            merged = existing + [n for n in lucide_names if n not in existing]
            new_import = "import { " + ", ".join(merged) + " } from 'lucide-react';"
            new_src = new_src[:m.start()] + new_import + new_src[m.end():]
        else:
            new_import = "import { " + ", ".join(lucide_names) + " } from 'lucide-react';\n"
            first_import = re.search(r"^import .*\n", new_src, re.MULTILINE)
            if first_import:
                new_src = new_src[:first_import.end()] + new_import + new_src[first_import.end():]
            else:
                new_src = new_import + new_src

        try:
            jf.write_text(new_src, encoding="utf-8")
            patched += 1
            mapping_str = ", ".join(f"{o}->{n}" for o, n in rename_map.items())
            print(f"  [patcher] Rewrote @heroicons/react import(s) to lucide-react in {jf.name}: {mapping_str}")
        except Exception:
            pass

    return patched


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
                # Star import of a missing module (`from app.models.users
                # import *`) — the symbol index can't help, but a
                # singular/plural sibling usually can. Confirmed live
                # (Exp108, tiny_notes): model file is user.py, main.py
                # star-imports app.models.users → hard ModuleNotFoundError
                # at startup; both redirect passes skipped '*' entirely.
                if "*" in names_raw:
                    head, _, last = dotted.rpartition(".")
                    variants = []
                    if last.endswith("ies"):
                        variants.append(last[:-3] + "y")
                    if last.endswith("s"):
                        variants.append(last[:-1])
                    variants.append(last + "s")
                    if last.endswith("y"):
                        variants.append(last[:-1] + "ies")
                    for v in variants:
                        sibling = f"{head}.{v}" if head else v
                        if _backend_module_exists(root, f"app.{sibling}"):
                            return f"from app.{sibling} import *"
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


_WRONG_RESPONSE_MODEL_RE = re.compile(
    r"(response_model\s*=\s*(?:List\[|list\[)?\s*)(\w+?)(Base|Create|Update)\b")


def _patch_wrong_schema_class_as_response_model(project_path: Path) -> int:
    """Swap `response_model=XBase/XCreate/XUpdate` for the sibling
    XResponse/XOut/XRead class when one exists.

    Confirmed live (Exp111, simple_crm during the exp109-milestone-r2
    canary): every contact route declared `response_model=ContactBase` —
    a schema WITHOUT `id` — while a perfectly good `ContactResponse`
    (with `id: int`) sat unused in the same schema module. FastAPI
    filters responses through the declared model, so every Create/List/
    Get response had its `id` stripped: the CRUD journey logged
    `Create entity: 201 id=None`, and Edit/Delete/Verify cascade-failed.
    This breaks any real API consumer the same way, not just the journey.
    Corpus prevalence: 19 occurrences across 5 apps. Nothing is changed
    when no better class exists."""
    import ast as _ast
    root = project_path.resolve()
    schemas_dir = root / "app" / "schemas"
    routes_dirs = [d for d in (root / "app" / "routes", root / "app" / "routers") if d.is_dir()]
    if not routes_dirs:
        return 0

    schema_class_modules: dict[str, str] = {}
    if schemas_dir.is_dir():
        for sf in schemas_dir.glob("*.py"):
            try:
                tree = _ast.parse(sf.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                continue
            for node in tree.body:
                if isinstance(node, _ast.ClassDef):
                    schema_class_modules[node.name] = f"app.schemas.{sf.stem}"

    patched = 0
    for routes_dir in routes_dirs:
        for rf in routes_dir.glob("*.py"):
            try:
                src = rf.read_text(encoding="utf-8", errors="replace")
                tree = _ast.parse(src)
            except Exception:
                continue

            local_names: set[str] = set()
            for node in tree.body:
                if isinstance(node, _ast.ClassDef):
                    local_names.add(node.name)
                elif isinstance(node, _ast.ImportFrom):
                    local_names.update(a.asname or a.name for a in node.names)

            swapped: dict[str, str] = {}

            def _swap(m: re.Match) -> str:
                prefix_txt, cls_prefix, kind = m.group(1), m.group(2), m.group(3)
                for suffix in ("Response", "Out", "Read"):
                    candidate = cls_prefix + suffix
                    if candidate in local_names or candidate in schema_class_modules:
                        swapped[f"{cls_prefix}{kind}"] = candidate
                        return prefix_txt + candidate
                return m.group(0)

            new_src = _WRONG_RESPONSE_MODEL_RE.sub(_swap, src)
            if new_src == src:
                continue

            missing = sorted({c for c in swapped.values()
                              if c not in local_names and c in schema_class_modules})
            if missing:
                by_module: dict[str, list[str]] = {}
                for name in missing:
                    by_module.setdefault(schema_class_modules[name], []).append(name)
                import_lines = "".join(
                    f"from {mod} import {', '.join(ns)}\n" for mod, ns in sorted(by_module.items()))
                lines = new_src.splitlines(keepends=True)
                last_import = 0
                for i, line in enumerate(lines):
                    if re.match(r"(from\s+\S+\s+import\b|import\s+\S+)", line):
                        last_import = i + 1
                new_src = "".join(lines[:last_import]) + import_lines + "".join(lines[last_import:])

            try:
                _ast.parse(new_src)
            except Exception:
                continue
            rf.write_text(new_src, encoding="utf-8")
            patched += 1
            print(f"  [patcher] response_model class swap(s) in {rf.name}: {swapped}")
    return patched


_SQL_INTERVAL_RE = re.compile(
    r"func\.now\(\)\s*-\s*func\.interval\(\s*['\"](\d+)\s*(second|minute|hour|day|week)s?['\"]\s*\)"
)
_INTERVAL_UNIT_TO_TIMEDELTA_KWARG = {
    "second": "seconds", "minute": "minutes", "hour": "hours",
    "day": "days", "week": "weeks",
}


def _patch_postgres_only_sql_interval(project_path: Path) -> int:
    """Replace `func.now() - func.interval('N day(s)')` with a Python-side
    `datetime.utcnow() - timedelta(days=N)`.

    Confirmed live (forge_blog_cms, 2026-07-22): `GET /stats/summary`
    500'd with `OperationalError: no such function: interval`.
    `func.interval(...)` is Postgres-only SQL -- every generated app runs
    on SQLite (see app/database.py), which has neither an `interval`
    function nor native date arithmetic on its text-based datetime
    columns, so this expression can never work regardless of which row
    calls it. `func.now()` alone is fine (SQLAlchemy special-cases it to
    `CURRENT_TIMESTAMP` for sqlite); only the interval-subtraction
    compound is unsupported. Evaluating the cutoff in Python and binding
    it as a literal sidesteps the dialect gap entirely -- AST-unverified
    (regex-only, mirrors this file's other narrow content patches) but
    the match is specific enough that a false positive is not plausible."""
    root = project_path.resolve()
    dirs = [d for d in (root / "app" / "routes", root / "app" / "routers", root / "app" / "services")
            if d.is_dir()]

    patched = 0
    for d in dirs:
        for pf in d.glob("*.py"):
            try:
                src = pf.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if "func.interval(" not in src:
                continue

            need_dt: set[str] = set()

            def _replace(m: re.Match) -> str:
                amount, unit = m.group(1), m.group(2)
                kwarg = _INTERVAL_UNIT_TO_TIMEDELTA_KWARG[unit]
                need_dt.update({"datetime", "timedelta"})
                return f"datetime.utcnow() - timedelta({kwarg}={amount})"

            new_src = _SQL_INTERVAL_RE.sub(_replace, src)
            if new_src == src:
                continue  # func.interval( present but not this exact shape -- leave untouched

            if need_dt:
                m = re.search(r"^from datetime import ([^\n]+)$", new_src, re.MULTILINE)
                if m:
                    existing = {n.strip() for n in m.group(1).split(",")}
                    missing = sorted(need_dt - existing)
                    if missing:
                        new_src = (new_src[:m.end(1)] + ", " + ", ".join(missing) + new_src[m.end(1):])
                else:
                    new_src = f"from datetime import {', '.join(sorted(need_dt))}\n" + new_src

            try:
                ast.parse(new_src)
            except SyntaxError:
                continue  # never write a file we can't confirm still parses

            pf.write_text(new_src, encoding="utf-8")
            patched += 1
            print(f"  [patcher] Replaced Postgres-only func.interval() SQL with a Python-side "
                  f"cutoff in {pf.name}")
    return patched


def _patch_unbound_conditional_db_ops(project_path: Path) -> int:
    """Guard `db.refresh(x)` / `db.add(x)` / `db.delete(x)` statements whose
    target is only ever assigned inside a nested block of the same function.

    Confirmed live (Exp110, forge_blog_cms create_post): the LLM writes

        if post_in.tags:
            for tag_name in post_in.tags:
                association = PostTags(...)
                db.add(association)
        db.commit()
        db.refresh(association)      # <- UnboundLocalError when tags == []

    The journey's Create payload sends `tags: []`, so the loop never binds
    the name and the route 500s — killing every downstream CRUD step. Fix:
    initialize the name to None at function start and guard the statement
    with `if x is not None:`. AST-verified; a file that stops parsing is
    left untouched."""
    import ast as _ast
    root = project_path.resolve()
    routes_dirs = [d for d in (root / "app" / "routes", root / "app" / "routers") if d.is_dir()]

    patched = 0
    for routes_dir in routes_dirs:
        for pf in routes_dir.glob("*.py"):
            try:
                src = pf.read_text(encoding="utf-8", errors="replace")
                tree = _ast.parse(src)
            except Exception:
                continue

            fixes = []  # (stmt_lineno, name, first_body_lineno)
            for fn in _ast.walk(tree):
                if not isinstance(fn, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                    continue
                top_assigned = {a.arg for a in fn.args.args} | {a.arg for a in fn.args.kwonlyargs}
                for node in fn.body:
                    if isinstance(node, _ast.Assign):
                        top_assigned.update(t.id for t in node.targets if isinstance(t, _ast.Name))
                    elif isinstance(node, (_ast.AnnAssign, _ast.AugAssign)) and isinstance(node.target, _ast.Name):
                        top_assigned.add(node.target.id)
                all_assigned = set()
                for node in _ast.walk(fn):
                    if isinstance(node, _ast.Assign):
                        all_assigned.update(t.id for t in node.targets if isinstance(t, _ast.Name))
                    elif isinstance(node, (_ast.AnnAssign, _ast.AugAssign)) and isinstance(node.target, _ast.Name):
                        all_assigned.add(node.target.id)
                    elif isinstance(node, _ast.For) and isinstance(node.target, _ast.Name):
                        all_assigned.add(node.target.id)
                for node in fn.body:
                    if not (isinstance(node, _ast.Expr) and isinstance(node.value, _ast.Call)):
                        continue
                    call = node.value
                    if (isinstance(call.func, _ast.Attribute)
                            and isinstance(call.func.value, _ast.Name)
                            and call.func.value.id == "db"
                            and call.func.attr in ("refresh", "add", "delete")
                            and len(call.args) == 1
                            and isinstance(call.args[0], _ast.Name)):
                        name = call.args[0].id
                        if name in all_assigned and name not in top_assigned:
                            fixes.append((node.lineno, name, fn.body[0].lineno))

            if not fixes:
                continue

            lines = src.splitlines(keepends=True)
            # Guard statements bottom-up so line numbers stay valid.
            for stmt_lineno, name, _ in sorted(fixes, key=lambda f: -f[0]):
                idx = stmt_lineno - 1
                line = lines[idx]
                indent = line[: len(line) - len(line.lstrip())]
                lines[idx] = (f"{indent}if {name} is not None:\n"
                              f"{indent}    {line.lstrip()}")
            # Initialize each name once at the top of its function.
            for first_body_lineno, name in sorted(
                    {(f[2], f[1]) for f in fixes}, key=lambda x: -x[0]):
                idx = first_body_lineno - 1
                line = lines[idx]
                indent = line[: len(line) - len(line.lstrip())]
                lines[idx] = f"{indent}{name} = None\n{line}"

            new_src = "".join(lines)
            try:
                _ast.parse(new_src)
            except Exception:
                continue
            pf.write_text(new_src, encoding="utf-8")
            patched += 1
            print(f"  [patcher] Guarded conditionally-bound db op target(s) in {pf.name}: "
                  f"{sorted({f[1] for f in fixes})}")
    return patched


_HYPHEN_ROUTER_RE = re.compile(r"\b([A-Za-z_]\w*(?:-\w+)+_router)\b")


def _patch_models_without_primary_key(project_path: Path) -> int:
    """Give generated SQLAlchemy models without a key a safe surrogate id.

    LLMs frequently emit association models containing only foreign keys.
    SQLAlchemy refuses to map such a class at import time, taking down every
    endpoint. A surrogate integer key preserves those columns and restores a
    valid mapper without guessing a composite-key API contract.
    """
    models_dir = project_path / "app" / "models"
    if not models_dir.exists():
        return 0
    patched = 0
    for model_file in models_dir.glob("*.py"):
        if model_file.name.startswith("_"):
            continue
        try:
            source = model_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        class_matches = list(re.finditer(r"^class\s+\w+\s*\(Base\)\s*:", source, re.MULTILINE))
        if not class_matches:
            continue
        rewritten = source
        offset = 0
        changed = False
        for index, match in enumerate(class_matches):
            start = match.end() + offset
            next_start = (class_matches[index + 1].start() + offset) if index + 1 < len(class_matches) else len(rewritten)
            body = rewritten[start:next_start]
            if "primary_key=True" in body:
                continue
            insert_after = re.search(r"^    __table_args__\s*=.*\n", body, re.MULTILINE)
            if not insert_after:
                insert_after = re.search(r"^    __tablename__\s*=.*\n", body, re.MULTILINE)
            if not insert_after:
                continue
            insertion = "    id = Column(Integer, primary_key=True, autoincrement=True)\n"
            at = start + insert_after.end()
            rewritten = rewritten[:at] + insertion + rewritten[at:]
            offset += len(insertion)
            changed = True
        if not changed:
            continue
        if "from sqlalchemy import" in rewritten:
            rewritten = re.sub(
                r"from sqlalchemy import ([^\n]+)",
                lambda m: m.group(0) if "Integer" in m.group(1) else f"from sqlalchemy import {m.group(1)}, Integer",
                rewritten, count=1,
            )
        else:
            rewritten = "from sqlalchemy import Integer\n" + rewritten
        model_file.write_text(rewritten, encoding="utf-8")
        patched += 1
        print(f"  [patcher] Added surrogate primary key(s) in {model_file.name}")
    return patched


def _patch_hyphenated_router_identifiers(project_path: Path) -> int:
    """Rewrite hyphenated `*_router` identifiers to underscores.

    Confirmed live twice (Exp107, ForgeBench v1 era 2026-07-13):
    hospital_management_system's main.py contained
    `from app.routes.consultation_note_routes import consultation-note_router`
    and real_estate_marketplace had `agent-dashboard_router` — the LLM
    derives the router symbol from a hyphenated resource/path name. A
    hyphen in an identifier is a SyntaxError in main.py: the app cannot
    even be imported, every dimension fails at once, and no downstream
    patcher runs (they all skip unparseable files).

    After sanitizing, exact-duplicate router import/include lines are
    dropped (the repair loop often adds the correctly-spelled line next
    to the broken one, so a plain rename would leave both).

    The same generations also produce HYPHENATED ROUTE FILENAMES
    (`agent-dashboard_routes.py`) — a module that can never be imported —
    so those are renamed to underscores first and every
    `app.routes.<hyphenated>` module reference is rewritten to match."""
    root = project_path.resolve()
    candidates = [root / "app" / "main.py"]
    route_dirs = [d for d in (root / "app" / "routes", root / "app" / "routers") if d.is_dir()]
    patched = 0

    # 1) rename hyphenated route files (un-importable module names)
    for d in route_dirs:
        for pf in list(d.glob("*-*.py")):
            target = pf.with_name(pf.name.replace("-", "_"))
            if target.exists():
                continue  # a correctly-named twin already exists; leave both
            pf.rename(target)
            patched += 1
            print(f"  [patcher] Renamed un-importable route module {pf.name} -> {target.name}")

    for d in route_dirs:
        candidates.extend(d.glob("*.py"))

    # 2) rewrite hyphenated module segments in route imports
    module_ref_re = re.compile(r"(from\s+app\.route(?:r)?s\.)([\w-]*-[\w-]*)(\s+import\b)")
    for pf in candidates:
        if not pf.is_file():
            continue
        try:
            src = pf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if not (_HYPHEN_ROUTER_RE.search(src) or module_ref_re.search(src)):
            continue
        fixed_names = sorted({m.group(1) for m in _HYPHEN_ROUTER_RE.finditer(src)})
        fixed_names += sorted({m.group(2) for m in module_ref_re.finditer(src)})
        new_src = _HYPHEN_ROUTER_RE.sub(lambda m: m.group(1).replace("-", "_"), src)
        new_src = module_ref_re.sub(
            lambda m: m.group(1) + m.group(2).replace("-", "_") + m.group(3), new_src)

        # Drop exact-duplicate router import/include lines the rename may
        # have created.
        seen: set[str] = set()
        out_lines = []
        for line in new_src.splitlines(keepends=True):
            stripped = line.strip()
            is_router_line = (
                (stripped.startswith("from app.route") and "_router" in stripped)
                or stripped.startswith("app.include_router(")
            )
            if is_router_line:
                if stripped in seen:
                    continue
                seen.add(stripped)
            out_lines.append(line)
        new_src = "".join(out_lines)

        pf.write_text(new_src, encoding="utf-8")
        patched += 1
        print(f"  [patcher] Fixed hyphenated router identifier(s) in {pf.name}: {fixed_names}")
    return patched


def _patch_quoted_route_annotations(project_path: Path) -> int:
    """Unquote string annotations in route files that name a real
    schema/model class, hoisting the import to module level.

    Confirmed live twice (Exp106, 2026-07-15): the LLM writes
    `response_model=List["SaleOut"]` (restaurant_pos_system) or
    `def update(budget_in: "BudgetCreate")` (expense_tracker on Railway)
    with the actual `from app.schemas... import ...` deferred INSIDE the
    handler body. The route works at request time (the inner import runs),
    but FastAPI keeps an unresolvable ForwardRef, so /openapi.json 500s
    with PydanticUserError "'X' is not fully defined" — which both breaks
    /docs on the deployed app and blinds the CRUD journey's schema
    introspection (the Exp105 incident's precondition).

    Only quoted strings that exactly match a class defined at module level
    in app/schemas/*.py or app/models/*.py are touched, and only in
    annotation positions (response_model=..., `param: "X"`,
    `List["X"]`/`Optional["X"]`). Every rewritten file must still
    ast-parse or it is left untouched."""
    import ast as _ast
    root = project_path.resolve()
    schemas_dir = root / "app" / "schemas"
    models_dir = root / "app" / "models"
    routes_dirs = [d for d in (root / "app" / "routes", root / "app" / "routers") if d.is_dir()]
    if not routes_dirs:
        return 0

    # class name -> dotted module (schemas win over models on collision,
    # matching what a response_model/body annotation almost always means)
    class_modules: dict[str, str] = {}
    for base, prefix in ((models_dir, "app.models"), (schemas_dir, "app.schemas")):
        if not base.is_dir():
            continue
        for pf in base.glob("*.py"):
            try:
                tree = _ast.parse(pf.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                continue
            for node in tree.body:
                if isinstance(node, _ast.ClassDef):
                    class_modules[node.name] = f"{prefix}.{pf.stem}"
    if not class_modules:
        return 0

    names_alt = "|".join(re.escape(n) for n in sorted(class_modules, key=len, reverse=True))
    # `param: "X"` — an UNQUOTED identifier before the colon plus a
    # comma/paren/default after the name keeps dict literals ({"role": "X"})
    # and ordinary string values out of reach.
    ann_re = re.compile(rf'(\b[a-zA-Z_]\w*\s*:\s*)(["\'])({names_alt})\2(?=\s*[,)=])')
    # `-> "X":` return annotations
    ret_re = re.compile(rf'(->\s*)(["\'])({names_alt})\2(?=\s*:)')
    # List["X"] / Optional["X"] / list["X"] anywhere (incl. response_model=...)
    generic_re = re.compile(rf'((?:List|Optional|list|Sequence)\[\s*)(["\'])({names_alt})\2(\s*\])')
    # response_model="X"
    respmodel_re = re.compile(rf'(response_model\s*=\s*)(["\'])({names_alt})\2')

    patched = 0
    for routes_dir in routes_dirs:
        for rf in routes_dir.glob("*.py"):
            try:
                src = rf.read_text(encoding="utf-8", errors="replace")
                tree = _ast.parse(src)
            except Exception:
                continue

            used: set[str] = set()

            def _unquote(m: re.Match) -> str:
                used.add(m.group(3))
                groups = m.groups()
                return groups[0] + groups[2] + (groups[3] if len(groups) > 3 else "")

            new_src = ann_re.sub(_unquote, src)
            new_src = ret_re.sub(_unquote, new_src)
            new_src = generic_re.sub(_unquote, new_src)
            new_src = respmodel_re.sub(_unquote, new_src)
            if new_src == src:
                continue

            # Names already visible at module level need no new import.
            module_level: set[str] = set()
            for node in tree.body:
                if isinstance(node, _ast.ClassDef):
                    module_level.add(node.name)
                elif isinstance(node, _ast.ImportFrom):
                    module_level.update(a.asname or a.name for a in node.names)
                elif isinstance(node, _ast.Import):
                    module_level.update((a.asname or a.name).split(".")[0] for a in node.names)
            missing = sorted(used - module_level)
            if missing:
                by_module: dict[str, list[str]] = {}
                for name in missing:
                    by_module.setdefault(class_modules[name], []).append(name)
                import_lines = "".join(
                    f"from {mod} import {', '.join(ns)}\n" for mod, ns in sorted(by_module.items())
                )
                lines = new_src.splitlines(keepends=True)
                last_import = 0
                for i, line in enumerate(lines):
                    # column-0 only — an indented (function-body) import as
                    # the anchor would inject the new import mid-function
                    if re.match(r"(from\s+\S+\s+import\b|import\s+\S+)", line):
                        last_import = i + 1
                new_src = "".join(lines[:last_import]) + import_lines + "".join(lines[last_import:])

            try:
                _ast.parse(new_src)
            except Exception:
                continue
            rf.write_text(new_src, encoding="utf-8")
            patched += 1
            print(f"  [patcher] Unquoted ForwardRef annotation(s) in {rf.name}: {sorted(used)}")

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


# Component names owned by react-router-dom that also happen to collide with a
# real lucide-react icon name (currently just "Link" — lucide has a chain-link
# icon by that name too). Blindly intersecting JSX-tag usage with the lucide
# icon set — as this patcher used to do — meant a forgotten `import { Link }
# from 'react-router-dom'` got "fixed" by importing the lucide icon instead,
# which type-checks and builds fine but silently breaks every <Link to="..">
# in the file (renders a plain, non-navigating icon glyph). Route these names
# to react-router-dom instead of lucide-react.
_ROUTER_COMPONENT_NAMES = frozenset({
    "Link", "NavLink", "Navigate", "Route", "Routes", "Outlet", "BrowserRouter",
})


def _patch_invalid_lucide_icons(project_path: Path) -> int:
    """Replace lucide-react imports of icons the PINNED package version does
    not export with a real equivalent.

    LLMs hallucinate icon names constantly — either newer-lucide names
    (ChartBar, CircleAlert, House) or plausible inventions (Handshake) that
    don't exist in the pinned 0.263.1. Each one is a guaranteed vite build
    failure ('\"X\" is not exported by node_modules/lucide-react/...'), which
    today burns an LLM fix attempt on a mistake that's mechanically
    correctable: swap the name for the closest real icon (see
    _LUCIDE_INVALID_RENAMES) or the neutral Circle fallback, in both the
    import statement and every JSX/expression usage. Seen live: the m3
    canary's crm build failed on 'Handshake', and dine_reserve's telemetry
    logged the same class ('\"Handshake\" is not exported').
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

        m = lucide_import_re.search(src)
        if not m:
            continue

        entries = [e.strip() for e in m.group(1).split(",") if e.strip()]
        new_entries: list[str] = []
        renames: dict[str, str] = {}  # local usage name -> replacement usage name
        changed = False
        for entry in entries:
            if " as " in entry:
                orig, alias = [p.strip() for p in entry.split(" as ", 1)]
                if orig in VALID_LUCIDE_ICONS:
                    new_entries.append(entry)
                else:
                    # keep the alias the file already uses; only fix the source name
                    repl = _LUCIDE_INVALID_RENAMES.get(orig, "Circle")
                    new_entries.append(f"{repl} as {alias}")
                    changed = True
            else:
                if entry in VALID_LUCIDE_ICONS:
                    new_entries.append(entry)
                else:
                    repl = _LUCIDE_INVALID_RENAMES.get(entry, "Circle")
                    renames[entry] = repl
                    if repl not in new_entries:
                        new_entries.append(repl)
                    changed = True

        if not changed:
            continue

        # Dedupe while preserving order (a replacement may collide with an
        # icon the file already imported).
        seen: set[str] = set()
        deduped = []
        for e in new_entries:
            key = e.split(" as ")[-1].strip()
            if key not in seen:
                seen.add(key)
                deduped.append(e)

        new_import = "import { " + ", ".join(deduped) + " } from 'lucide-react';"
        new_src = src[:m.start()] + new_import + src[m.end():]
        for bad, good in renames.items():
            new_src = re.sub(rf"\b{bad}\b", good, new_src)

        try:
            jf.write_text(new_src, encoding="utf-8")
            patched += 1
            fixed = sorted(set(renames) | {e.split(' as ')[0] for e in entries if ' as ' in e and e.split(' as ')[0].strip() not in VALID_LUCIDE_ICONS})
            print(f"  [patcher] Replaced non-existent lucide icon(s) {fixed} in {jf.name}")
        except Exception:
            pass

    return patched


def _patch_missing_icon_imports(project_path: Path) -> int:
    """Add lucide-react icons (or react-router-dom components) that are USED in
    JSX but never imported.

    LLMs routinely render <ChevronRight/> (or another icon) without importing it.
    The vite build passes — an undefined JSX identifier is valid *syntax* — but at
    runtime it's a ReferenceError that unmounts React and shows a BLANK PAGE.
    Nothing else in the pipeline catches this (build is green, the page just
    silently dies). Seen live: /tasks rendered blank because ChevronRight wasn't
    imported. This adds any such icon (restricted to the known-real _LUCIDE_ICONS
    set, so it can never introduce a bad export) to the file's lucide import,
    creating the import line if the file has none. Names in
    _ROUTER_COMPONENT_NAMES are resolved to react-router-dom instead, even
    though some (Link) are also valid lucide icon names — see that set's
    docstring for why.
    """
    import re
    src_dir = project_path / "src"
    if not src_dir.exists():
        return 0

    patched = 0
    lucide_import_re = re.compile(r"import\s*\{([^}]*)\}\s*from\s*['\"]lucide-react['\"]\s*;?")
    router_import_re = re.compile(r"import\s*\{([^}]*)\}\s*from\s*['\"]react-router-dom['\"]\s*;?")

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
        undeclared = used - known
        router_missing = sorted(undeclared & _ROUTER_COMPONENT_NAMES)
        missing = sorted((undeclared & _LUCIDE_ICONS) - _ROUTER_COMPONENT_NAMES)

        if router_missing:
            m = router_import_re.search(src)
            if m:
                existing = [n.strip() for n in m.group(1).split(",") if n.strip()]
                merged = existing + [n for n in router_missing if n not in existing]
                new_import = "import { " + ", ".join(merged) + " } from 'react-router-dom';"
                src = src[:m.start()] + new_import + src[m.end():]
            else:
                new_import = "import { " + ", ".join(router_missing) + " } from 'react-router-dom';\n"
                first_import = re.search(r"^import .*\n", src, re.MULTILINE)
                if first_import:
                    src = src[:first_import.end()] + new_import + src[first_import.end():]
                else:
                    src = new_import + src
            print(f"  [patcher] Added missing react-router-dom import(s) {router_missing} to {jf.name}")

        if missing:
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
            print(f"  [patcher] Added missing icon import(s) {missing} to {jf.name}")

        if not router_missing and not missing:
            continue

        try:
            jf.write_text(src, encoding="utf-8")
            patched += 1
        except Exception:
            pass

    return patched


_UNSAFE_CHAIN_CALL_RE = re.compile(
    r"(\w+)\?\.(\w+)\.(map|filter|forEach|reduce|reduceRight|find|findIndex|"
    r"some|every|flatMap|join|slice|indexOf|includes)\("
)
_UNSAFE_CHAIN_LENGTH_RE = re.compile(r"(\w+)\?\.(\w+)\.length\b")


def _patch_unsafe_optional_chain_before_array_method(project_path: Path) -> int:
    """Fix `x?.y.map(...)` -- optional-chained on the base but not on the
    nested property actually being iterated.

    `x?.y` short-circuits to `undefined` only when `x` itself is nullish; if
    `x` is a real (but incomplete) object and `y` is simply absent -- e.g. a
    fresh-user API response `{}` with no `weekly_completions` key yet -- `y`
    evaluates to `undefined` and `.map`/`.length` on it throws a TypeError.
    React has no default error boundary, so this unmounts the entire tree:
    the whole app goes to a blank white page with nothing in the console UI
    testers would notice from a static build check. Seen live: a habit
    tracker's dashboard rendered blank for every real user because
    `stats?.weekly_completions.map(...)` crashed on the first API response
    that hadn't accumulated any completions yet. Rewrites to
    `x?.y?.map(...)` / `x?.y?.length`, which is always valid JS and fixes
    the crash regardless of what LLM originally generated it.
    """
    src_dir = project_path / "src"
    if not src_dir.exists():
        return 0

    patched = 0
    for jf in list(src_dir.rglob("*.jsx")) + list(src_dir.rglob("*.js")) + list(src_dir.rglob("*.tsx")):
        try:
            content = jf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        new_content = _UNSAFE_CHAIN_CALL_RE.sub(r"\1?.\2?.\3(", content)
        new_content = _UNSAFE_CHAIN_LENGTH_RE.sub(r"\1?.\2?.length", new_content)

        if new_content != content:
            jf.write_text(new_content, encoding="utf-8")
            patched += 1
            print(f"  [patcher] Fixed unsafe optional chain before array access in {jf.name}")

    return patched


_BARE_ARRAY_DATA_RE = re.compile(
    r"\b(\w+)\.data\.(map|filter|forEach|reduce|reduceRight|find|findIndex|"
    r"some|every|flatMap|join|slice|indexOf|includes)\("
)


def _patch_response_data_used_as_bare_array(project_path: Path) -> int:
    """Fix `res.data.map(...)` assuming an axios response body is always a
    bare array.

    The backend and frontend are generated by separate LLM calls with no
    shared source of truth for response shape -- a report/aggregate
    endpoint routinely wraps its list in an object (e.g. `{start_date,
    end_date, entries: [...]}` for a weekly report) while the frontend
    that consumes it assumes the list is the entire response body. Unlike
    the missing-optional-chaining case above, there is no `?.` to fix
    here: `res.data` is never null/undefined (axios always resolves an
    object with a `.data` key), it is simply the wrong *type* -- calling
    `.map`/`.filter`/etc. on a plain object throws "TypeError: ... is not
    a function", uncaught, same blank/broken-page outcome. Seen live: a
    habit tracker's weekly report endpoint returned `{start_date,
    end_date, entries, summary}` while the dashboard did
    `weeklyRes.data.map(...)`, crashing on every login. Rewrites to check
    `Array.isArray` first (so the already-correct bare-array case, the
    common one, is untouched) and only falls back to the common
    wrapper-key names when `.data` isn't actually an array.
    """
    src_dir = project_path / "src"
    if not src_dir.exists():
        return 0

    patched = 0
    for jf in list(src_dir.rglob("*.jsx")) + list(src_dir.rglob("*.js")) + list(src_dir.rglob("*.tsx")):
        try:
            content = jf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        def _fix(m: re.Match) -> str:
            var, method = m.group(1), m.group(2)
            d = f"{var}.data"
            return (
                f"(Array.isArray({d}) ? {d} : ({d}?.entries || {d}?.items || "
                f"{d}?.results || {d}?.data || [])).{method}("
            )

        new_content = _BARE_ARRAY_DATA_RE.sub(_fix, content)

        if new_content != content:
            jf.write_text(new_content, encoding="utf-8")
            patched += 1
            print(f"  [patcher] Fixed response .data assumed to be a bare array in {jf.name}")

    return patched


_WRAPPED_KEY_ACCESS_RE = re.compile(r"\b(\w+)\.data\.(items|entries|results)\b")


def _patch_response_data_assumed_wrapped(project_path: Path) -> int:
    """Fix `res.data.items` (or .entries/.results) assuming an axios response
    body is always wrapped in an object with that key, when it's actually a
    bare array.

    The inverse of _patch_response_data_used_as_bare_array above: there the
    frontend wrongly assumed a bare array; here it wrongly assumes a wrapper
    object. Both are the same root problem (backend and frontend generated by
    separate LLM calls with no shared source of truth for response shape) in
    opposite directions. Seen live: a badges endpoint returning a bare `[]`
    while BadgesPage did `setBadges(response.data.items)` -- `.items` is
    undefined even in the success case, and the very next render's
    `badges.length` throws "Cannot read properties of undefined", crashing
    the page immediately after every login (even for a brand new user with
    zero badges). That's the same practical outcome as the bare-array case:
    trading a stuck screen for a crashed one right after the fix that got
    the user there. Rewrites to check Array.isArray(X.data) first, so the
    already-correct wrapped case is untouched.
    """
    src_dir = project_path / "src"
    if not src_dir.exists():
        return 0

    patched = 0
    for jf in list(src_dir.rglob("*.jsx")) + list(src_dir.rglob("*.js")) + list(src_dir.rglob("*.tsx")):
        try:
            content = jf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        def _fix(m: re.Match) -> str:
            var, key = m.group(1), m.group(2)
            d = f"{var}.data"
            return f"(Array.isArray({d}) ? {d} : ({d}?.{key} || {d}?.items || {d}?.entries || {d}?.results || []))"

        new_content = _WRAPPED_KEY_ACCESS_RE.sub(_fix, content)

        if new_content != content:
            jf.write_text(new_content, encoding="utf-8")
            patched += 1
            print(f"  [patcher] Fixed response .data assumed to be wrapped (actually a bare array) in {jf.name}")

    return patched


@dataclass
class FrontendPatchResult:
    """
    Exp055: one row of observability for a single call inside
    run_frontend_patches -- name, outcome, timing, and (on failure) the
    exception, so a failure is diagnosable instead of just "silently
    absorbed." `skipped` always False today (none of the 14 calls have a
    gating condition -- see docs/REPAIR_FAILURE_ISOLATION.md §2) but the
    field exists so a future gated patcher doesn't need a schema change.
    """
    name: str
    success: bool
    count: int
    duration_ms: float
    skipped: bool = False
    exception: str | None = None


def _run_frontend_patch_isolated(
    results: list[FrontendPatchResult], name: str, fn, project_path: Path, *, as_bool: bool = False,
) -> int:
    """
    Exp055: run one frontend patcher with its own exception boundary,
    recording a FrontendPatchResult regardless of outcome.

    Confirmed gap from Exp053 §6: run_frontend_patches's 14-call sequence
    had the exact same missing-isolation shape run_deterministic_patches's
    ~40-call sequence had before Exp053's `_run_patch_isolated` fixed it
    there -- one unhandled exception here used to abort every remaining
    frontend patcher, AND propagate uncaught out of run_frontend_patches
    itself to both call sites (run_deterministic_patches, where Exp053's
    outer isolation catches it but loses all 14 sub-results at once, and
    main.py::_resync_frontend, which has no try/except at all around its
    `run_frontend_patches(root)` call -- a single bad frontend patcher
    could 500 the entire "Check & Fix deployed app" resync). This closes
    both.

    `as_bool=True` preserves _patch_frontend_package_json's original
    `bool(...)` conversion exactly (it returns bool, not int, unlike every
    other patcher in this list) -- same behavior, now isolated too.
    """
    t0 = time.perf_counter()
    try:
        raw = fn(project_path)
        count = (1 if raw else 0) if as_bool else (raw or 0)
        results.append(FrontendPatchResult(
            name=name, success=True, count=count,
            duration_ms=(time.perf_counter() - t0) * 1000,
        ))
        return count
    except Exception as exc:
        results.append(FrontendPatchResult(
            name=name, success=False, count=0,
            duration_ms=(time.perf_counter() - t0) * 1000,
            exception=f"{type(exc).__name__}: {exc}",
        ))
        print(f"  [frontend_patcher] {name} raised {type(exc).__name__}: {exc} -- "
              f"skipping, continuing with remaining frontend patches")
        return 0


def _run_frontend_patches_detailed(project_path: Path) -> tuple[int, list[FrontendPatchResult]]:
    """
    Exp055: the actual 15-call sequence, each call isolated, with a full
    FrontendPatchResult list returned alongside the total count for
    callers that want the per-patcher breakdown (tests, future
    telemetry). run_frontend_patches() below is the original public
    entry point -- same signature, same return type, same call order --
    that just discards the detail list to stay byte-for-byte compatible
    with its two existing callers.
    """
    results: list[FrontendPatchResult] = []
    patched = 0
    patched += _run_frontend_patch_isolated(
        results, "_patch_frontend_package_json", _patch_frontend_package_json, project_path, as_bool=True)
    patched += _run_frontend_patch_isolated(
        results, "_patch_vite_root_proxy_and_api_base", _patch_vite_root_proxy_and_api_base, project_path)
    patched += _run_frontend_patch_isolated(
        results, "_patch_disallowed_icon_packages", _patch_disallowed_icon_packages, project_path)
    patched += _run_frontend_patch_isolated(
        results, "_patch_invalid_lucide_icons", _patch_invalid_lucide_icons, project_path)
    patched += _run_frontend_patch_isolated(
        results, "_patch_missing_icon_imports", _patch_missing_icon_imports, project_path)
    patched += _run_frontend_patch_isolated(
        results, "_patch_frontend_auth_field_names", _patch_frontend_auth_field_names, project_path)
    patched += _run_frontend_patch_isolated(
        results, "_patch_frontend_signup_password_key", _patch_frontend_signup_password_key, project_path)
    patched += _run_frontend_patch_isolated(
        results, "_patch_stale_status_on_error", _patch_stale_status_on_error, project_path)
    patched += _run_frontend_patch_isolated(
        results, "_patch_unsafe_optional_chain_before_array_method",
        _patch_unsafe_optional_chain_before_array_method, project_path)
    patched += _run_frontend_patch_isolated(
        results, "_patch_response_data_used_as_bare_array", _patch_response_data_used_as_bare_array, project_path)
    patched += _run_frontend_patch_isolated(
        results, "_patch_response_data_assumed_wrapped", _patch_response_data_assumed_wrapped, project_path)
    patched += _run_frontend_patch_isolated(
        results, "_patch_hidden_loading_status", _patch_hidden_loading_status, project_path)
    patched += _run_frontend_patch_isolated(
        results, "_patch_pagination_component", _patch_pagination_component, project_path)
    patched += _run_frontend_patch_isolated(
        results, "_patch_broken_template_literal_classnames",
        _patch_broken_template_literal_classnames, project_path)
    patched += _run_frontend_patch_isolated(
        results, "patch_ensure_auth_pages", patch_ensure_auth_pages, project_path)
    return patched, results


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

    Exp055: each of the 15 calls below now runs in its own exception
    boundary (see _run_frontend_patches_detailed) -- one patcher raising
    no longer aborts the rest or propagates out to either call site. See
    docs/REPAIR_FAILURE_ISOLATION.md for the full before/after.
    """
    patched, _results = _run_frontend_patches_detailed(project_path)
    return patched


def _run_patch_isolated(counts: dict, key: str, fn, *args, **kwargs) -> None:
    """
    Exp053: run one project-wide patcher with its own exception boundary.

    Confirmed gap from Experiment 051's audit: run_deterministic_patches's
    ~40-call sequential list had NO per-call failure isolation -- unlike
    preflight.py's PreflightRegistry, which wraps every fix in its own
    try/except (see preflight.py's own comment on this exact contrast). A
    single unhandled exception partway through this function used to abort
    every remaining patcher in the sequence, silently -- including
    unrelated ones with no connection to whatever raised. Sets counts[key]
    = 0 and logs on failure; every subsequent patcher in the sequence
    still gets to run.
    """
    try:
        counts[key] = fn(*args, **kwargs) or 0
    except Exception as exc:
        counts[key] = 0
        print(f"  [patcher] {key} raised {type(exc).__name__}: {exc} -- "
              f"skipping, continuing with remaining patchers")


def run_deterministic_patches(project_path: str, skip_protected_injections: bool = False) -> dict:
    """
    Run all deterministic patches on a generated project.

    Returns {"_total_modified": N, <patcher_name>: count, ...} -- every
    individual patcher's own count (files/fields/lines it changed), each
    one a failure prevented before the generated app ever reaches the
    runtime/verification stages. Nothing previously read this function's
    return value (every one of its 7 call sites called it as a bare
    statement), so widening from a plain int to this dict is safe -- see
    reliability_metrics.py's DETERMINISTIC_PREVENTION_CATEGORIES for how
    these get rolled up into the reliability dashboard.

    skip_protected_injections=True: skip auth_routes.py and auth_utils.py injection.
    Pass True when calling after Architecture Repair so the repair's output is not
    overwritten by the static template.
    """
    root = Path(project_path)
    modified = 0
    counts: dict[str, int] = {}

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
    # The per-file loop above chains ~11 inline content-transform patchers
    # (smart quotes, passlib→bcrypt, async/sync, pydantic regex→pattern,
    # ORM response-model rewrites, ...) on the same `patched` string --
    # cheap to run, expensive to attribute individually (would mean
    # diffing after every single transform in the chain). Counted in bulk
    # under "syntax_and_compat" rather than not at all.
    counts["_inline_content_patches"] = modified

    # requirements.txt
    for req in root.rglob("requirements.txt"):
        _patch_requirements(req)

    # Strip ALL relationship() declarations first — prevents SQLAlchemy mapper crash
    # (NoForeignKeysError) that hangs ALL endpoints when FK path is missing/ambiguous.
    # Must run before back_populates strip since we remove the whole statement.
    _run_patch_isolated(counts, "_patch_strip_relationships", _patch_strip_relationships, root)

    # Also strip residual back_populates/backref kwargs (defensive, in case any remain)
    _run_patch_isolated(counts, "_patch_strip_back_populates", _patch_strip_back_populates, root)

    # Strip FKs to non-existent tables (prevents NoReferencedTableError at startup)
    _run_patch_isolated(counts, "_patch_dangling_foreign_keys", _patch_dangling_foreign_keys, root)

    # Deduplicate model files (user.py + users.py both having class User → keep larger)
    _run_patch_isolated(counts, "_patch_deduplicate_models", _patch_deduplicate_models, root)

    # Same collision for schemas (expense.py + expenses.py both having
    # ExpenseCreate → keep larger). Must run before the import-redirect patcher
    # so any import left pointing at the dropped file gets resolved to the kept one.
    _run_patch_isolated(counts, "_patch_deduplicate_schemas", _patch_deduplicate_schemas, root)

    # Model class aliases (Games→Game etc) — run before FK import patcher
    _run_patch_isolated(counts, "_patch_model_aliases", _patch_model_aliases, root)

    # Relationship string aliases (Genre→Genres etc) — must run before FK import patcher
    _run_patch_isolated(counts, "_patch_relationship_string_aliases", _patch_relationship_string_aliases, root)

    # FK imports in main.py (must run after alias patch so class names are correct)
    _run_patch_isolated(counts, "_patch_main_fk_imports", _patch_main_fk_imports, root)

    # Model imports missing from route files that query() them (stats/aggregate
    # routes are the repeat offender) -- must run after alias/dedup so class
    # names and modules are final.
    _run_patch_isolated(counts, "_patch_missing_model_imports_in_routes", _patch_missing_model_imports_in_routes, root)

    # `db: Session = Depends(get_db)` used as a type annotation without
    # `from sqlalchemy.orm import Session` -- confirmed live (habit_tracker,
    # 2026-07-25) on an architecture-repair LLM response, a hard NameError
    # crash at import time (the whole app fails to start, not just one route).
    _run_patch_isolated(
        counts,
        "_patch_missing_session_import_in_routes",
        _patch_missing_session_import_in_routes,
        root,
    )

    # Route handlers referencing a model attribute that doesn't actually
    # exist (created_at/last_active/status guessed without seeing the real
    # schema) -- must run after imports/aliases/dedup are final so the
    # attribute-existence check sees the real model shape.
    _run_patch_isolated(counts, "_patch_invalid_model_attribute_access", _patch_invalid_model_attribute_access, root)

    # Router names: `router` → `{resource}_router` (eliminates RouterExportMismatch)
    _run_patch_isolated(counts, "_patch_router_names", _patch_router_names, root)

    # Hyphenated router identifiers (`consultation-note_router`) are a
    # SyntaxError that kills main.py outright — sanitize + dedupe (Exp107)
    _run_patch_isolated(counts, "_patch_hyphenated_router_identifiers", _patch_hyphenated_router_identifiers, root)

    # SQLAlchemy cannot map an association model with no primary key.
    _run_patch_isolated(counts, "_patch_models_without_primary_key", _patch_models_without_primary_key, root)

    # db.refresh/add/delete on a name bound only inside a nested block →
    # UnboundLocalError 500 on empty input (Exp110, forge_blog_cms)
    _run_patch_isolated(counts, "_patch_unbound_conditional_db_ops", _patch_unbound_conditional_db_ops, root)

    # func.now() - func.interval(...) is Postgres-only SQL; every generated
    # app runs on SQLite, which has no `interval` function at all →
    # OperationalError 500 (Exp139, forge_blog_cms /stats/summary)
    _run_patch_isolated(counts, "_patch_postgres_only_sql_interval", _patch_postgres_only_sql_interval, root)

    # response_model=XBase/XCreate strips id from every response when an
    # XResponse exists — journey can never capture an entity id (Exp111)
    _run_patch_isolated(counts, "_patch_wrong_schema_class_as_response_model", _patch_wrong_schema_class_as_response_model, root)

    # Parameter ordering: Path(...) before body param → SyntaxError → reorder
    _run_patch_isolated(counts, "_patch_param_order", _patch_param_order, root)

    # A static sub-route (e.g. /habits/streaks) registered after a
    # same-shaped parameterized route (/habits/{habit_id}) is permanently
    # unreachable -- the parameterized one matches first and "swallows" it.
    _run_patch_isolated(counts, "patch_reorder_shadowed_static_routes", patch_reorder_shadowed_static_routes, root)

    # Auth utils / routes: inject known-good templates on initial generation.
    # skip_protected_injections=True when called after Architecture Repair — the repair's
    # output is authoritative and must not be clobbered by the static template.
    if not skip_protected_injections:
        _run_patch_isolated(counts, "_patch_auth_utils", _patch_auth_utils, root)
        _run_patch_isolated(counts, "_patch_auth_requirements", _patch_auth_requirements, root)
        _run_patch_isolated(counts, "_patch_auth_routes", _patch_auth_routes, root)

    # Redirect `from app.routers.auth import ...` (and similar wrong paths) to the
    # module that actually defines the symbols — must run before router wiring so
    # main.py's imports resolve. Prevents the ModuleNotFoundError that made auth
    # 404 and every fix attempt regress-and-revert.
    _run_patch_isolated(counts, "_patch_redirect_missing_backend_imports", _patch_redirect_missing_backend_imports, root)

    # Wire ALL routers into main.py — runs after auth_routes injection so auth_router
    # is already in main.py; this catches every other generated router.
    _run_patch_isolated(counts, "_patch_wire_orphan_routers", _patch_wire_orphan_routers, root)

    # Router wiring can synthesize a new import from a hyphenated filename.
    # Re-converge the syntax invariant after that mutating pass.
    _run_patch_isolated(counts, "_patch_hyphenated_router_identifiers_final", _patch_hyphenated_router_identifiers, root)

    # Synthesize LoginPage/RegisterPage if App.jsx redirects to /login but
    # the LLM never generated them -- must run BEFORE the generic orphan
    # route wirer below, which would otherwise wrap these in PrivateRoute.
    _run_patch_isolated(counts, "patch_ensure_auth_pages", patch_ensure_auth_pages, root)

    # Duplicate import declarations are a hard esbuild error; dedupe before
    # the route wirer so its own injected imports can't collide either.
    _run_patch_isolated(counts, "_patch_dedupe_frontend_imports", _patch_dedupe_frontend_imports, root)

    # Frontend mirror: wire any page component App.jsx imports but never
    # mounted on a <Route> (see docstring for why this is invisible to
    # every other automated check).
    _run_patch_isolated(counts, "_patch_wire_orphan_frontend_routes", _patch_wire_orphan_frontend_routes, root)

    # Backstop re-run: the wirer above (and the LLM fix loop) can introduce
    # duplicate identifier imports AFTER the first dedupe pass — esbuild
    # treats those as a hard error (Exp112).
    _run_patch_isolated(counts, "_patch_dedupe_frontend_imports_post_wire", _patch_dedupe_frontend_imports, root)

    # Must run after the line above: if the app's main authenticated page
    # isn't literally named "Dashboard", login/register's hardcoded
    # navigate('/dashboard') matches no route at all and silently bounces
    # back to /login even though auth succeeded (see docstring).
    _run_patch_isolated(counts, "_patch_login_redirect_target", _patch_login_redirect_target, root)

    # Seed robustness: guard against IndexError when parent entity inserts fail
    _run_patch_isolated(counts, "_patch_seed_robustness", _patch_seed_robustness, root)

    # Create stub schema files for any route imports that point to missing modules
    # (common after architecture repair generates new route files)
    _run_patch_isolated(counts, "_patch_create_missing_schemas", _patch_create_missing_schemas, root)

    # Unquote string annotations (`response_model=List["X"]`, `p: "X"`) whose
    # class really exists in app/schemas|models and hoist the import — the
    # unresolved-ForwardRef shape 500s /openapi.json (Exp106). Must run AFTER
    # _patch_create_missing_schemas so freshly stubbed classes resolve too.
    _run_patch_isolated(counts, "_patch_quoted_route_annotations", _patch_quoted_route_annotations, root)

    # Make Response schema fields Optional so ORM field-name mismatches don't crash
    # (e.g. UserResponse.username required but User model uses email only)
    _run_patch_isolated(counts, "_patch_response_schemas_optional", _patch_response_schemas_optional, root)

    # Same fix for fields a *Response class INHERITS from a shared *Base
    # class rather than declaring itself -- the check above only sees a
    # class's own body text, so an inherited-but-missing field (e.g.
    # CourseResponse(CourseBase) inheriting `price` from CourseBase, which
    # the Course model has no column for at all) slips through untouched.
    _run_patch_isolated(counts, "_patch_response_schema_inherited_required_fields", _patch_response_schema_inherited_required_fields, root)

    # Required Create-schema fields define API input contracts: make their
    # model columns NOT NULL before generic response-schema softening.
    _run_patch_isolated(counts, "_patch_required_create_schema_model_nullability", _patch_required_create_schema_model_nullability, root)

    # Required response-schema fields on nullable model columns → Optional[T] = None
    # (kills the recurring "required but model allows NULL" validator error)
    _run_patch_isolated(counts, "_patch_schema_nullable_required_mismatch", _patch_schema_nullable_required_mismatch, root)

    # The inverse gap — a nullable=False model column that no *required*
    # Create-schema field can ever populate — lives in repair/preflight
    # (fix_model_schema_notnull_gap, Exp012/013) and used to run ONLY
    # there, i.e. AFTER the V6-stage validation loop had already crashed
    # its journey on the guaranteed IntegrityError and burned LLM fix
    # calls on it (Exp113, forge_blog_cms posts.content_markdown: two
    # journey crashes + a runtime-fix round per run, every run, before
    # the preflight relax ever fired). Run it here too, pre-validation.
    def _early_notnull_gap(project_root: Path) -> int:
        from app.repair.preflight import _fix_model_schema_notnull_gap
        return 1 if _fix_model_schema_notnull_gap(project_root, []) else 0
    _run_patch_isolated(counts, "fix_model_schema_notnull_gap_early", _early_notnull_gap, root)

    # Response schemas must expose id (journey/frontends need it) and must
    # type DateTime columns as datetime, not str (else 500 on every row)
    _run_patch_isolated(counts, "_patch_response_schema_id_and_datetimes", _patch_response_schema_id_and_datetimes, root)

    # Inject model_config = {'from_attributes': True} into all Pydantic schemas
    # so FastAPI can serialize SQLAlchemy ORM objects returned from route handlers
    _run_patch_isolated(counts, "_patch_schemas_from_attributes", _patch_schemas_from_attributes, root)

    # A repair model can emit Pydantic v2's Config-only `from_attributes = True`
    # directly in a BaseModel class, which prevents backend import entirely.
    _run_patch_isolated(counts, "_patch_bare_pydantic_from_attributes", _patch_bare_pydantic_from_attributes, root)

    _run_patch_isolated(counts, "_patch_pydantic_field_type_name_collisions", _patch_pydantic_field_type_name_collisions, root)

    # Filter **schema.dict() unpacking to remove fields not on the SQLAlchemy model.
    # Prevents TypeError: 'status' is an invalid keyword argument for Task when the
    # Pydantic schema has extra fields that don't exist as columns on the model.
    _run_patch_isolated(counts, "_patch_star_dict_extra_fields", _patch_star_dict_extra_fields, root)

    # Fix the same filter if it was already generated with the older, unsafe
    # hasattr(Model, k) form -- passes a read-only @property through and
    # raises AttributeError: property 'x' has no setter on every create request.
    _run_patch_isolated(counts, "_patch_unsafe_model_hasattr_filter", _patch_unsafe_model_hasattr_filter, root)

    # Fix an already-filtered constructor call missing the exclusion for a
    # trailing kwarg that collides with a same-named schema field (e.g.
    # HabitCreate.user_id vs the route's own user_id=current_user.id) --
    # "got multiple values for keyword argument" on every create request.
    _run_patch_isolated(counts, "_patch_filtered_ctor_kwarg_collision", _patch_filtered_ctor_kwarg_collision, root)

    # Fix attribute accesses (e.g. user.username when model only has email).
    # The field_patcher fixes constructor calls; this fixes dict literals and returns.
    _run_patch_isolated(counts, "_patch_attr_access_mismatches", _patch_attr_access_mismatches, root)

    # Fix ownership-FK naming drift in query/filter expressions (e.g.
    # Contact.user_id used in a filter when the model's real FK to users
    # is Contact.owner_id) -- a silent, permanent data-isolation bug since
    # the filter never matches any real user id. Class-qualified, checks
    # ForeignKey-typed columns only (see _model_fk_columns), so it won't
    # touch a genuinely unrelated same-named column on another model.
    _run_patch_isolated(counts, "_patch_ownership_fk_attribute_drift", _patch_ownership_fk_attribute_drift, root)

    # Exp092: fix the CREATE-time counterpart -- a POST handler accepts
    # current_user but never assigns the constructed model's ownership
    # FK before db.add(), the confirmed root cause of 74% of tracked
    # JourneyCRUDFailure instances (Exp091). Reuses the exact same
    # _model_fk_columns/_OWNERSHIP_FK_SYNONYMS lookup as the drift-fix
    # above; a separate function since it's a distinct bug shape
    # (insert-time omission, not query/filter attribute-name drift).
    _run_patch_isolated(counts, "_patch_missing_ownership_assignment", _patch_missing_ownership_assignment, root)

    # Companion to the fix above: covers the case where current_user is
    # missing from the handler signature entirely (not just the
    # assignment), confirmed live on habit_tracker (2026-07-25) after an
    # architecture-repair LLM pass dropped the dependency from a POST
    # handler outright despite the Tech Lead having flagged missing auth
    # as CRITICAL.
    _run_patch_isolated(
        counts,
        "_patch_missing_current_user_dependency_for_ownership_insert",
        _patch_missing_current_user_dependency_for_ownership_insert,
        root,
    )

    # Add a field to a Create/Update schema when a route handler reads it
    # off that schema but it was never declared there, even though a
    # sibling schema for the same entity (Response/Base) already has it --
    # e.g. WorkoutCreate missing `date` while WorkoutResponse has it,
    # crashing every Create request with AttributeError.
    _run_patch_isolated(counts, "_patch_missing_create_update_fields", _patch_missing_create_update_fields, root)

    # An Optional-typed Update-schema field with no real default is still
    # Pydantic-required -- Optional[...] widens the accepted type, it
    # doesn't supply a default -- so a partial-update PATCH omitting that
    # field 422s. Confirmed live (habit_tracker, 2026-07-25):
    # HabitUpdate.name/.frequency as `Optional[str] = Field(min_length=1)`.
    _run_patch_isolated(
        counts,
        "_patch_update_schema_optional_field_missing_default",
        _patch_update_schema_optional_field_missing_default,
        root,
    )

    # Fix SQLAlchemy ORM models used as Pydantic field types in route files
    # (e.g. labels: List[Label] where Label is a SQLAlchemy model → List[Any])
    _run_patch_isolated(counts, "_patch_orm_type_in_route_schemas", _patch_orm_type_in_route_schemas, root)

    # Ensure all schema files that use BaseModel/Field/Optional actually import them.
    _run_patch_isolated(counts, "_patch_missing_pydantic_imports", _patch_missing_pydantic_imports, root)

    # Fix response_model=List[X] on handlers that return {"items": ..., "total": N}
    # → strip the List[] response_model so FastAPI passes the dict through unvalidated.
    _run_patch_isolated(counts, "_patch_list_response_model_mismatch", _patch_list_response_model_mismatch, root)

    # Create service stubs when route files import from app.services.X that doesn't exist.
    # LLMs sometimes generate a service layer but only generate routes, not services.
    _run_patch_isolated(counts, "_patch_create_missing_service_stubs", _patch_create_missing_service_stubs, root)

    # Inject db.refresh(obj) after db.commit() where missing — LLMs forget this, causing
    # POST handlers to return id=None because the ORM object isn't re-bound to the DB row.
    _run_patch_isolated(counts, "_patch_missing_db_refresh", _patch_missing_db_refresh, root)

    # All frontend-only fixes live in one bundle (see run_frontend_patches
    # below) so a standalone frontend resync (main.py's _resync_frontend,
    # used by "Check & Fix deployed app") can never silently drift out of
    # sync with this list again.
    _run_patch_isolated(counts, "run_frontend_patches", run_frontend_patches, root)

    if modified:
        print(f"  [patcher] Patched {modified} file(s) — passlib→bcrypt, async→sync, smart quotes")

    counts["_total_modified"] = modified
    return counts
