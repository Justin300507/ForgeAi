import ast
import os
import re
import shutil
import time

from app.templates.database_template import DATABASE_PY_TEMPLATE
from app.templates.frontend_templates import FRONTEND_TEMPLATE_FILES, PWA_EXTRA_FILES

_CREATE_ALL_SNIPPET = "\nfrom app.database import Base, engine\nBase.metadata.create_all(bind=engine)\n"


def _strip_trailing_backslash_space(content: str) -> str:
    lines = content.split("\n")
    return "\n".join(line.rstrip() for line in lines)


def _repair_backslash_syntax(content: str) -> str:
    """Multi-strategy repair for LLM-generated Python with backslash encoding errors.

    Root cause: LLM double-escapes JSON — writes \\"value\\" instead of "value".
    In Python source this appears as backslash+quote, which the tokenizer treats
    as a broken line-continuation → SyntaxError: unexpected character after line
    continuation character.
    """
    # Strategy 0: fix double-escaped quotes \" → " (most common LLM mistake)
    if '\\"' in content:
        s0 = content.replace('\\"', '"')
        try:
            ast.parse(s0)
            return s0
        except SyntaxError:
            pass
        # Strategy 0+1: also strip trailing whitespace
        s01 = "\n".join(line.rstrip() for line in s0.split("\n"))
        try:
            ast.parse(s01)
            return s01
        except SyntaxError:
            pass

    # Strategy 1: strip trailing whitespace (fixes `\ ` at end of line)
    s1 = "\n".join(line.rstrip() for line in content.split("\n"))
    try:
        ast.parse(s1)
        return s1
    except SyntaxError:
        pass

    # Strategy 2: replace literal \n (backslash+n chars) with real newlines.
    s2 = s1.replace("\\n", "\n").replace("\\t", "    ")
    try:
        ast.parse(s2)
        return s2
    except SyntaxError:
        pass

    return s1  # at least strip trailing whitespace


# Auth-only schema class names — should only appear in auth_routes.py or schemas/auth.py,
# never in other schema files like user.py, candidate_profile.py, etc.
_AUTH_SCHEMA_CLASSES = frozenset({
    "LoginRequest", "RegisterRequest", "Token", "AuthToken", "AuthResponse",
    "AuthRegisterRequest", "AuthLoginRequest", "AuthCreate", "AuthUpdate",
    "LoginResponse", "RegisterResponse", "TokenResponse", "AuthUser",
})


def _strip_auth_classes_from_schema(path: str, content: str) -> str:
    """Remove auth schema classes from non-auth schema files to prevent duplicate_class errors.
    schemas/auth.py is canonical. Other schema files (user.py, etc.) should not
    re-define LoginRequest, Token, etc."""
    norm = path.replace("\\", "/")
    fname = norm.split("/")[-1]
    # Only apply to schema files that are NOT auth.py
    if "/schemas/" not in norm or fname == "auth.py":
        return content
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return content
    lines = content.splitlines(keepends=True)
    remove_lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name in _AUTH_SCHEMA_CLASSES:
            # Remove from lineno-1 to end_lineno (0-indexed)
            remove_lines.update(range(node.lineno - 1, node.end_lineno))
    if not remove_lines:
        return content
    result = [line for i, line in enumerate(lines) if i not in remove_lines]
    stripped = "".join(result)
    # Clean up consecutive blank lines left by removal
    stripped = re.sub(r"\n{3,}", "\n\n", stripped)
    return stripped


def _add_extend_existing(content: str) -> str:
    """Add __table_args__ = {'extend_existing': True} to SQLAlchemy models.
    Prevents duplicate-table crashes when two files define the same __tablename__."""
    if "__tablename__" not in content:
        return content
    if "extend_existing" in content or "__table_args__" in content:
        return content
    return re.sub(
        r'(__tablename__\s*=\s*["\'][^"\']+["\'])',
        r'\1\n    __table_args__ = {"extend_existing": True}',
        content,
    )


def _fix_pydantic_v1_patterns(content: str) -> str:
    """Fix Pydantic v1 patterns that crash under Pydantic v2.

    1. constr(regex=...) → constr(pattern=...) (Pydantic v2 renamed the param)
    2. Remove lookahead/lookbehind from pattern= strings (Rust regex doesn't support them)
    """
    # Fix regex= → pattern= inside constr() calls
    if "constr" in content and "regex=" in content:
        content = re.sub(
            r'\bconstr\(([^)]*)\bregex\s*=',
            lambda m: m.group(0).replace('regex=', 'pattern='),
            content,
        )

    # Strip lookahead/lookbehind from ANY pattern= value in the file.
    # Pydantic v2 uses the Rust regex engine which does NOT support lookaheads.
    if "(?=" in content or "(?<" in content or "(?!" in content:
        def _strip_lookahead(m):
            # Remove (?=...) (?<=...) (?!...) (?<!...) groups from the matched pattern string
            return re.sub(r'\(\?[=!<][^)]*\)', '', m.group(0))
        content = re.sub(r'pattern\s*=\s*r?["\'][^"\']*["\']', _strip_lookahead, content)

    return content


def _fix_smart_quotes(content: str) -> str:
    """Replace Unicode smart quotes/dashes with ASCII equivalents in JSX/JS files."""
    replacements = [
        ('‘', "'"), ('’', "'"),  # '' left/right single
        ('“', '"'), ('”', '"'),  # "" left/right double
        ('–', '-'), ('—', '--'), # en-dash, em-dash
        ('…', '...'),                 # ellipsis
    ]
    for old, new in replacements:
        content = content.replace(old, new)
    return content


def _fix_double_depends(content: str) -> str:
    """Fix Depends(Depends(X)) → Depends(X). FastAPI crashes if given a Depends object
    where it expects a callable (the inner Depends wraps the function, outer double-wraps it)."""
    prev = None
    while prev != content:
        prev = content
        content = re.sub(r'Depends\(Depends\(([^()]+)\)\)', r'Depends(\1)', content)
    return content


def _strip_invalid_eager_loading(content: str) -> str:
    """Remove .options(selectinload/joinedload(...)) calls that would crash when the
    model attribute doesn't exist. Lazy loading is used instead (safe default)."""
    # Pattern: .options( one or more selectinload/joinedload calls )
    # Handles single and chained options in one .options() call
    content = re.sub(
        r'\.options\(\s*(?:(?:selectinload|joinedload|subqueryload|immediateload)\([^)]+\)\s*,?\s*)+\)',
        '',
        content,
    )
    # Clean up orphan imports of these loaders if no longer used
    for loader in ("selectinload", "joinedload", "subqueryload", "immediateload"):
        if loader not in content:
            content = re.sub(rf',\s*{loader}\b', '', content)
            content = re.sub(rf'\b{loader}\s*,\s*', '', content)
    return content


def _normalize_newlines(path, content):
    if "\\n" in content and "\n" not in content:
        content = content.replace("\\n", "\n").replace("\\t", "\t")
    if path.endswith((".jsx", ".js")):
        if '\\"' in content:
            content = content.replace('\\"', '"')
        # Fix Unicode smart quotes that break esbuild
        content = _fix_smart_quotes(content)
    if path.endswith(".py"):
        content = _repair_backslash_syntax(content)
        content = _add_extend_existing(content)
        content = _strip_auth_classes_from_schema(path, content)
        content = _fix_pydantic_v1_patterns(content)
        if "/routes/" in path.replace("\\", "/"):
            content = _strip_invalid_eager_loading(content)
            content = _fix_double_depends(content)
    if path.endswith(".py") and "schemas." in content:
        content = _fix_schemas_namespace(content)
    return content


def _fix_schemas_namespace(content: str) -> str:
    """
    Detect `schemas.resource.ClassName` patterns and rewrite them to direct imports.
    E.g.: response_model=schemas.user.UserResponse
      → adds: from app.schemas.user import UserResponse
      → replaces: schemas.user.UserResponse → UserResponse
    """
    pattern = re.compile(r'\bschemas\.(\w+)\.(\w+)\b')
    matches = pattern.findall(content)
    if not matches:
        return content

    # Collect unique (module, class) pairs
    imports_needed = {}
    for module, cls in matches:
        imports_needed.setdefault(module, set()).add(cls)

    # Replace all occurrences in content
    def replacer(m):
        return m.group(2)  # just the class name
    content = pattern.sub(replacer, content)

    # Inject missing imports at the top (after existing imports)
    new_import_lines = []
    for module, classes in imports_needed.items():
        for cls in sorted(classes):
            import_line = f"from app.schemas.{module} import {cls}"
            if import_line not in content:
                new_import_lines.append(import_line)

    if new_import_lines:
        lines = content.splitlines(keepends=True)
        insert_idx = 0
        for i, line in enumerate(lines):
            stripped = line.lstrip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                insert_idx = i + 1
        for import_line in reversed(new_import_lines):
            lines.insert(insert_idx, import_line + "\n")
        content = "".join(lines)

    return content


def _ensure_create_all(path, content):
    """Ensure main.py always calls Base.metadata.create_all so SQLite tables exist."""
    if not path.endswith("app/main.py") and not path.endswith("app\\main.py"):
        return content
    if "create_all" in content:
        return content
    # Insert after the last import block, before any app = FastAPI() line
    lines = content.splitlines(keepends=True)
    insert_idx = 0
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("import ") or stripped.startswith("from "):
            insert_idx = i + 1
    lines.insert(insert_idx, _CREATE_ALL_SNIPPET)
    return "".join(lines)


def _is_safe_to_write(path, content):

    if not path.endswith(".py"):
        return True

    try:
        ast.parse(content)
        return True

    except SyntaxError as e:
        print(f"\n=== SKIPPING TRUNCATED/INVALID FILE: {path} ===")
        print(f"SyntaxError: {e}")
        # Dump ALL lines with repr so we can see the actual characters
        lines = content.split("\n")
        start = max(0, (e.lineno or 1) - 3) if e.lineno else 0
        end = min(len(lines), (e.lineno or len(lines)) + 2) if e.lineno else min(10, len(lines))
        for i, ln in enumerate(lines[start:end], start=start + 1):
            marker = " >>>" if i == e.lineno else "    "
            print(f"  {marker} L{i}: {repr(ln)}")
        if not e.lineno:
            # No lineno — print first 15 lines to debug
            for i, ln in enumerate(lines[:15], start=1):
                print(f"      L{i}: {repr(ln)}")
        return False


def write_files(project_name, files, frontend_target: str = "web"):

    project_name = (
        project_name
        .replace(" ", "_")
        .lower()
    )

    base_dir = os.path.abspath(
        os.path.join(
            "..",
            "generated_projects",
            project_name
        )
    )

    if os.path.exists(base_dir):
        import stat

        def _force_remove(func, path, excinfo):
            # Windows: .git objects are read-only — clear the flag then retry
            try:
                os.chmod(path, stat.S_IWRITE)
                func(path)
            except Exception:
                pass

        for attempt in range(3):
            try:
                shutil.rmtree(base_dir, onerror=_force_remove)
                break
            except (PermissionError, OSError) as e:
                if attempt == 2:
                    print(f"rmtree failed after 3 attempts — writing files in-place: {e}")
                    break
                print(f"rmtree failed ({e}) — retrying in 1s...")
                time.sleep(1)

    os.makedirs(
        base_dir,
        exist_ok=True
    )

    database_file_seen = False

    for file in files:

        path = file["path"]
        content = file["content"]

        if path == "app/database.py":
            content = DATABASE_PY_TEMPLATE
            database_file_seen = True

        content = _normalize_newlines(path, content)
        content = _ensure_create_all(path, content)

        if not _is_safe_to_write(path, content):
            continue

        full_path = os.path.join(
            base_dir,
            path
        )

        os.makedirs(
            os.path.dirname(full_path),
            exist_ok=True
        )

        with open(
            full_path,
            "w",
            encoding="utf-8"
        ) as f:

            f.write(content)

    if not database_file_seen:

        full_path = os.path.join(
            base_dir,
            "app/database.py"
        )

        os.makedirs(
            os.path.dirname(full_path),
            exist_ok=True
        )

        with open(
            full_path,
            "w",
            encoding="utf-8"
        ) as f:

            f.write(DATABASE_PY_TEMPLATE)

    # Write static Vite/React scaffolding so `npm run build` works
    template_files = dict(FRONTEND_TEMPLATE_FILES)
    if frontend_target == "pwa":
        template_files.update(PWA_EXTRA_FILES)  # PWA overrides index.html + adds extras

    for rel_path, content in template_files.items():
        full_path = os.path.join(base_dir, rel_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)

    # Write deterministic deployment configs (render.yaml, .env.example, GitHub Actions, etc.)
    # These override any LLM-generated versions which are often wrong.
    try:
        from app.services.deployment_config_service import generate_deployment_configs
        generate_deployment_configs(base_dir, project_name)
    except Exception as e:
        print(f"  [V14] Deployment configs skipped: {e}")

    return base_dir