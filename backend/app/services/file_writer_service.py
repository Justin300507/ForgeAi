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


def _fix_indent_error(content: str) -> str:
    """Fix 'unexpected indent' SyntaxError by dedenting the offending block.

    The LLM occasionally wraps module-level code (CORS setup, router includes)
    inside an accidental indented block.  We detect the first bad indent and
    remove it from that line onward until indent drops back to zero.
    """
    for _ in range(5):  # up to 5 passes for nested bad indents
        try:
            ast.parse(content)
            return content
        except SyntaxError as exc:
            if "unexpected indent" not in str(exc) or not exc.lineno:
                return content
            lines = content.split("\n")
            idx = exc.lineno - 1
            if idx >= len(lines):
                return content
            bad_line = lines[idx]
            indent = len(bad_line) - len(bad_line.lstrip())
            if indent == 0:
                return content
            # Dedent the block: strip `indent` characters from this and following
            # lines that are at the same or deeper level.
            result = lines[:]
            i = idx
            while i < len(result):
                ln = result[i]
                if ln.strip() == "":
                    i += 1
                    continue
                ln_indent = len(ln) - len(ln.lstrip())
                if ln_indent >= indent:
                    result[i] = ln[indent:]
                    i += 1
                else:
                    break
            content = "\n".join(result)
    return content


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


def _auto_fix_missing_pydantic_import(content: str) -> str:
    """Add 'from pydantic import BaseModel' if BaseModel is used but not imported."""
    if "BaseModel" not in content:
        return content
    if re.search(r'from\s+pydantic\s+import\b.*\bBaseModel\b', content):
        return content
    lines = content.splitlines(keepends=True)
    insert_idx = 0
    in_multiline = False
    for i, line in enumerate(lines):
        stripped_r = line.rstrip()
        stripped_l = line.lstrip()
        if in_multiline:
            # End of multi-line import is a line containing ")"
            if ")" in stripped_l:
                insert_idx = i + 1
                in_multiline = False
            continue
        if stripped_l.startswith("from ") or stripped_l.startswith("import "):
            if stripped_r.endswith("("):
                # Multi-line import: from x import (
                in_multiline = True
            else:
                insert_idx = i + 1
    lines.insert(insert_idx, "from pydantic import BaseModel\n")
    return "".join(lines)


def _fix_fastapi_param_order(content: str) -> str:
    """Fix FastAPI route handlers where Path/Query/Depends params appear before body params.

    Python requires non-default params before default params. FastAPI route handlers
    often have `item_id: int = Path(...)` before `body: Schema` — reorder so body
    params (no default) come first.
    """
    try:
        ast.parse(content)
        return content  # already valid
    except SyntaxError as e:
        if "default" not in str(e).lower():
            return content

    def _split_params(params_str: str) -> list:
        """Split a comma-separated param string respecting nested parens."""
        params = []
        current: list = []
        depth = 0
        for ch in params_str:
            if ch in "([":
                depth += 1
            elif ch in ")]":
                depth -= 1
            if ch == "," and depth == 0:
                params.append("".join(current))
                current = []
            else:
                current.append(ch)
        if "".join(current).strip():
            params.append("".join(current))
        return params

    def _reorder(m: re.Match) -> str:
        prefix = m.group(1)   # e.g. "def update_task(\n"
        body = m.group(2)     # everything between outer parens
        suffix = m.group(3)   # "):"
        params = _split_params(body)
        no_def = [p for p in params if "=" not in p and p.strip()]
        has_def = [p for p in params if "=" in p and p.strip()]
        if not no_def or not has_def:
            return m.group(0)
        # Check ordering is actually wrong (last no-default appears after first with-default)
        positions = {p: i for i, p in enumerate(params)}
        last_no = max(positions[p] for p in no_def)
        first_yes = min(positions[p] for p in has_def)
        if last_no < first_yes:
            return m.group(0)  # already correct
        reordered = no_def + has_def
        new_body = ",".join(reordered)
        result = f"{prefix}{new_body}{suffix}"
        # Validate the fix before returning
        try:
            ast.parse(result)
            return result
        except SyntaxError:
            return m.group(0)

    # Match `def name( params ):` spanning multiple lines.
    # Group 2 can't use [^)]+ because params often contain ) (e.g. Depends(get_db)).
    # Strategy: find def...(, then collect the balanced paren content, then ):
    import re as _re
    lines = content.splitlines(keepends=True)
    result = []
    i = 0
    changed = False
    while i < len(lines):
        line = lines[i]
        if not _re.match(r'\s*def\s+\w+\s*\(', line):
            result.append(line)
            i += 1
            continue
        # Collect lines until the closing ): of the function signature
        sig_lines = [line]
        depth = line.count('(') - line.count(')')
        j = i + 1
        while j < len(lines) and depth > 0:
            sig_lines.append(lines[j])
            depth += lines[j].count('(') - lines[j].count(')')
            j += 1
        sig = "".join(sig_lines)
        # Try to extract and reorder params
        m = _re.match(r'(\s*def\s+\w+\s*\()(.*?)(\)\s*(?:->.*?)?\s*:)', sig, _re.DOTALL)
        if m:
            params = _split_params(m.group(2))
            no_def = [p for p in params if "=" not in p and p.strip()]
            has_def = [p for p in params if "=" in p and p.strip()]
            if no_def and has_def:
                positions = {p: k for k, p in enumerate(params)}
                last_no = max(positions[p] for p in no_def)
                first_yes = min(positions[p] for p in has_def)
                if last_no > first_yes:
                    reordered = no_def + has_def
                    # Preserve any trailing content after the regex match
                    # (e.g. the '\n' after '):' — without it the function body
                    # runs straight onto the same line as ':', causing a SyntaxError)
                    trailing = sig[m.end():]
                    new_sig = m.group(1) + ",".join(reordered) + m.group(3) + trailing
                    try:
                        # Validate with a dummy body since sig has no function body
                        ast.parse(new_sig.lstrip() + "\n    pass")
                        sig = new_sig
                        changed = True
                    except SyntaxError:
                        pass
        result.append(sig)
        i = j
    fixed = "".join(result)
    if changed:
        try:
            ast.parse(fixed)
            return fixed
        except SyntaxError:
            pass
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
        # Fix LLM typo: auth2_scheme (missing 'o') → oauth2_scheme
        # Use word boundary so we don't corrupt the correctly-spelled "oauth2_scheme"
        # ("auth2_scheme" is a substring of "oauth2_scheme" — naive string replace would double it)
        if re.search(r'\bauth2_scheme\b', content):
            content = re.sub(r'\bauth2_scheme\b', 'oauth2_scheme', content)
        # Auto-add missing pydantic BaseModel import
        content = _auto_fix_missing_pydantic_import(content)
        # Fix FastAPI param ordering: body params must come before Path/Query/Depends
        content = _fix_fastapi_param_order(content)
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
        # Try auto-repair strategies before giving up
        repaired = _repair_backslash_syntax(content)
        if repaired != content:
            try:
                ast.parse(repaired)
                return repaired   # caller receives fixed content
            except SyntaxError:
                pass

        if "unexpected indent" in str(e):
            fixed = _fix_indent_error(content)
            try:
                ast.parse(fixed)
                print(f"  [file_writer] Auto-fixed indent error in {path}")
                return fixed      # caller receives fixed content
            except SyntaxError:
                pass

        # Last resort: param ordering fix (catches cases where _normalize_newlines
        # applied the fix but the whole-file ast.parse still failed for other reasons)
        if "default" in str(e).lower():
            fixed = _fix_fastapi_param_order(content)
            if fixed != content:
                try:
                    ast.parse(fixed)
                    print(f"  [file_writer] Auto-fixed param order in {path}")
                    return fixed
                except SyntaxError:
                    pass

        print(f"\n=== SKIPPING TRUNCATED/INVALID FILE: {path} ===")
        print(f"SyntaxError: {e}")
        lines = content.split("\n")
        start = max(0, (e.lineno or 1) - 3) if e.lineno else 0
        end = min(len(lines), (e.lineno or len(lines)) + 2) if e.lineno else min(10, len(lines))
        for i, ln in enumerate(lines[start:end], start=start + 1):
            marker = " >>>" if i == e.lineno else "    "
            print(f"  {marker} L{i}: {repr(ln)}")
        if not e.lineno:
            for i, ln in enumerate(lines[:15], start=1):
                print(f"      L{i}: {repr(ln)}")
        return False


def write_files(project_name, files, frontend_target: str = "web", idea: str = ""):

    project_name = (
        project_name
        .replace(" ", "_")
        .lower()
    )

    _projects_root = os.environ.get(
        "GENERATED_PROJECTS_DIR",
        os.path.abspath(os.path.join("..", "generated_projects"))
    )
    base_dir = os.path.join(_projects_root, project_name)

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

        if path.replace("\\", "/") == "app/database.py":
            content = DATABASE_PY_TEMPLATE
            database_file_seen = True

        content = _normalize_newlines(path, content)
        content = _ensure_create_all(path, content)

        safe = _is_safe_to_write(path, content)
        if safe is False:
            continue
        if isinstance(safe, str):  # auto-repaired content returned
            content = safe

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

    # Overlay per-app themed scaffold (category brand color, style-pack fonts,
    # motion tokens, real app title). FORGE_THEMED_SCAFFOLD=0 rolls back to the
    # static indigo/Inter scaffold; any theming error also falls back silently.
    if os.environ.get("FORGE_THEMED_SCAFFOLD", "1") != "0":
        try:
            from app.templates.theme_builder import build_themed_templates
            template_files.update(build_themed_templates(idea, project_name, frontend_target))
        except Exception as e:
            print(f"  [V14] Themed scaffold skipped ({e}) — using static templates")

    if (idea or "").strip():
        try:
            from app.prompts.design_system import detect_category_key
            from app.prompts.style_system import select_style
            from app.memory.design_fingerprint import record_design
            record_design(project_name, detect_category_key(idea), select_style(idea))
        except Exception:
            pass  # telemetry only — never let this affect a generation run

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