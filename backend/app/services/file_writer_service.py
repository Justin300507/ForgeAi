import ast
import os
import re
import shutil
import time

from app.templates.database_template import DATABASE_PY_TEMPLATE
from app.templates.frontend_templates import FRONTEND_TEMPLATE_FILES

_CREATE_ALL_SNIPPET = "\nfrom app.database import Base, engine\nBase.metadata.create_all(bind=engine)\n"


def _normalize_newlines(path, content):
    # When the LLM double-escapes newlines it emits \\n (literal \n) instead of
    # a real newline. If the content has no real newlines but has literal \n, unescape.
    if "\\n" in content and "\n" not in content:
        content = content.replace("\\n", "\n").replace("\\t", "\t")
    # In JSX files the LLM sometimes double-escapes attribute quotes:
    #   placeholder=\"User\" instead of placeholder="User"
    # This is never valid JSX, so unconditionally unescape \" → " in JSX/JS files.
    if path.endswith((".jsx", ".js")) and '\\"' in content:
        content = content.replace('\\"', '"')
    # In Python route files, rewrite `schemas.X.YClass` namespace access → direct import.
    # The LLM sometimes generates `response_model=schemas.user.UserResponse` which
    # fails at runtime because `app.schemas` doesn't auto-import submodules.
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
        return False


def write_files(project_name, files):

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
        # On Windows, OneDrive or a recently-killed uvicorn may hold files open.
        # Retry rmtree up to 5 times; if it still fails, just overwrite in-place.
        for attempt in range(5):
            try:
                shutil.rmtree(base_dir)
                break
            except (PermissionError, OSError) as e:
                if attempt == 4:
                    print(f"rmtree failed after 5 attempts — writing files in-place: {e}")
                    break
                print(f"rmtree failed ({e}) — retrying in 2s...")
                time.sleep(2)

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
    for rel_path, content in FRONTEND_TEMPLATE_FILES.items():
        full_path = os.path.join(base_dir, rel_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)

    return base_dir