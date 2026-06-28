import ast
import os
import re

_CREATE_ALL_SNIPPET = "\nfrom app.database import Base, engine\nBase.metadata.create_all(bind=engine)\n"


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
            if ")" in stripped_l:
                insert_idx = i + 1
                in_multiline = False
            continue
        if stripped_l.startswith("from ") or stripped_l.startswith("import "):
            if stripped_r.endswith("("):
                in_multiline = True
            else:
                insert_idx = i + 1
    lines.insert(insert_idx, "from pydantic import BaseModel\n")
    return "".join(lines)


def _normalize_newlines(path, content):
    if "\\n" in content and "\n" not in content:
        content = content.replace("\\n", "\n").replace("\\t", "\t")
    if path.endswith((".jsx", ".js")) and '\\"' in content:
        content = content.replace('\\"', '"')
    if path.endswith(".py"):
        from app.services.file_writer_service import (
            _repair_backslash_syntax, _add_extend_existing,
            _fix_pydantic_v1_patterns, _strip_auth_classes_from_schema,
            _strip_invalid_eager_loading, _fix_double_depends,
        )
        content = _repair_backslash_syntax(content)
        content = _add_extend_existing(content)
        content = _strip_auth_classes_from_schema(path, content)
        content = _fix_pydantic_v1_patterns(content)
        # Fix LLM typo: auth2_scheme (missing 'o') → oauth2_scheme
        if re.search(r'\bauth2_scheme\b', content):
            content = re.sub(r'\bauth2_scheme\b', 'oauth2_scheme', content)
        # Auto-add missing pydantic BaseModel import
        content = _auto_fix_missing_pydantic_import(content)
        # Fix FastAPI param ordering: body params must come before Path/Query/Depends
        from app.services.file_writer_service import _fix_fastapi_param_order
        content = _fix_fastapi_param_order(content)
        if "/routes/" in path.replace("\\", "/"):
            content = _strip_invalid_eager_loading(content)
            content = _fix_double_depends(content)
    if path.endswith((".jsx", ".js")):
        from app.services.file_writer_service import _fix_smart_quotes
        content = _fix_smart_quotes(content)
    if path.endswith(".py") and "schemas." in content:
        from app.services.file_writer_service import _fix_schemas_namespace
        content = _fix_schemas_namespace(content)
    return content


def _ensure_create_all(path, content):
    """Ensure main.py always calls Base.metadata.create_all so SQLite tables exist."""
    if not path.endswith("app/main.py") and not path.endswith("app\\main.py"):
        return content
    if "create_all" in content:
        return content
    lines = content.splitlines(keepends=True)
    insert_idx = 0
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("import ") or stripped.startswith("from "):
            insert_idx = i + 1
    lines.insert(insert_idx, _CREATE_ALL_SNIPPET)
    return "".join(lines)


def _is_safe_to_write(path, content):
    """Refuse to write Python files that don't even parse. This is
    the truncation/completeness guard — a broken LLM response should
    never silently overwrite a working file."""

    if not path.endswith(".py"):
        return True

    try:
        ast.parse(content)
        return True

    except SyntaxError as e:
        print(f"\n=== REFUSING TRUNCATED/INVALID WRITE: {path} ===")
        print(f"SyntaxError: {e}")
        return False


def write_fix(project_path, fix):

    path = fix.get("path")
    content = fix.get("content")

    if not path or not content:
        print("write_fix called with missing path/content, skipping.")
        return False

    # Block path traversal attacks — LLMs occasionally generate "../" paths
    norm = os.path.normpath(path)
    if norm.startswith("..") or os.path.isabs(norm):
        print(f"write_fix: blocked suspicious path: {path!r}")
        return False

    # Always inject the known-good database.py — never let LLM fixes overwrite it
    norm_fwd = path.replace("\\", "/")
    if norm_fwd == "app/database.py":
        from app.services.database_patcher import patch_database_py
        patch_database_py(project_path)
        return True

    content = _normalize_newlines(path, content)
    content = _ensure_create_all(path, content)

    if not _is_safe_to_write(path, content):
        return False

    full_path = os.path.join(
        project_path,
        path
    )

    parent_dir = os.path.dirname(full_path)

    # When writing a nested module like app/utils/auth.py, Python requires app/utils/
    # to be a package directory — not a flat app/utils.py file. Remove the conflicting
    # flat file and create __init__.py so the directory becomes a proper package.
    flat_conflict = parent_dir + ".py"
    if os.path.isfile(flat_conflict):
        os.remove(flat_conflict)
        print(f"  [fix_writer] removed conflicting flat module {os.path.relpath(flat_conflict, project_path)}")

    os.makedirs(
        parent_dir,
        exist_ok=True
    )

    # Ensure the package __init__.py exists so Python treats the directory as a package
    init_file = os.path.join(parent_dir, "__init__.py")
    if not os.path.exists(init_file):
        open(init_file, "w").close()

    with open(
        full_path,
        "w",
        encoding="utf-8"
    ) as f:

        f.write(content)

    return True