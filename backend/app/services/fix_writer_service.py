import ast
import os
import re

_CREATE_ALL_SNIPPET = "\nfrom app.database import Base, engine\nBase.metadata.create_all(bind=engine)\n"


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

    content = _normalize_newlines(path, content)
    content = _ensure_create_all(path, content)

    if not _is_safe_to_write(path, content):
        return False

    full_path = os.path.join(
        project_path,
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

    return True