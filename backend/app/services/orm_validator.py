# app/services/orm_validator.py

import ast
import os
import re


_FLASK_SQLALCHEMY_IMPORT = re.compile(
    r"^\s*(?:from\s+flask_sqlalchemy\s+import|import\s+flask_sqlalchemy)\b",
    re.MULTILINE,
)
_FLASK_SQLALCHEMY_INIT = re.compile(
    r"\b\w+\s*=\s*SQLAlchemy\s*\(",
)
_FLASK_MODEL_BASE = re.compile(
    r"class\s+\w+\s*\(\s*(?:\w+\.)?db\.Model\b",
)


def validate_no_flask_sqlalchemy(project_path, errors):
    """The project contract requires plain SQLAlchemy (Base = declarative_base()),
    never Flask-SQLAlchemy (`db = SQLAlchemy()`, `class X(db.Model)`). An LLM
    occasionally hallucinates the Flask-SQLAlchemy API into a FastAPI project —
    this crashes at import time (flask_sqlalchemy isn't installed / wrong API)
    with no static check previously catching it before that runtime failure."""

    for root, _, files in os.walk(project_path):

        for file in files:

            if not file.endswith(".py"):
                continue

            file_path = os.path.join(root, file)

            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception:
                continue

            rel = os.path.relpath(file_path, project_path).replace(os.sep, "/")

            if _FLASK_SQLALCHEMY_IMPORT.search(content):
                errors.append(
                    f"Flask-SQLAlchemy usage: {rel} imports flask_sqlalchemy — "
                    f"this project must use plain SQLAlchemy (Base = declarative_base())"
                )
                continue

            if _FLASK_SQLALCHEMY_INIT.search(content):
                errors.append(
                    f"Flask-SQLAlchemy usage: {rel} calls SQLAlchemy(...) — "
                    f"this project must use plain SQLAlchemy, not Flask-SQLAlchemy"
                )
                continue

            if _FLASK_MODEL_BASE.search(content):
                errors.append(
                    f"Flask-SQLAlchemy usage: {rel} defines a class inheriting from "
                    f"db.Model — models must inherit from Base (app.database.Base)"
                )


def _get_class_bases(file_path):

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        tree = ast.parse(content)
    except Exception:
        return {}

    result = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            result[node.name] = {
                b.id for b in node.bases if isinstance(b, ast.Name)
            }
    return result


def _resolves_to_pydantic(project_path, module, class_name, cache, visiting=None):

    if visiting is None:
        visiting = set()

    key = (module, class_name)
    if key in cache:
        return cache[key]
    if key in visiting:
        return False
    visiting.add(key)

    file_path = os.path.join(
        project_path,
        module.replace(".", os.sep) + ".py"
    )

    bases_map = _get_class_bases(file_path)
    bases = bases_map.get(class_name, set())

    if "BaseModel" in bases:
        cache[key] = True
        return True

    for base in bases:
        if base in bases_map:
            if _resolves_to_pydantic(project_path, module, base, cache, visiting):
                cache[key] = True
                return True

    cache[key] = False
    return False


def validate_orm_usage(project_path, errors):

    cache = {}

    for root, _, files in os.walk(project_path):

        for file in files:

            if not file.endswith(".py"):
                continue

            file_path = os.path.join(root, file)

            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
                tree = ast.parse(content)
            except Exception:
                continue

            local_imports = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    for alias in node.names:
                        local_name = alias.asname or alias.name
                        local_imports[local_name] = node.module

            for name, module in local_imports.items():

                if not _resolves_to_pydantic(project_path, module, name, cache):
                    continue

                patterns = [
                    f"query({name})",
                    f"query({name},",
                    f".query({name})",
                    f".query({name},"
                ]

                for pattern in patterns:
                    if pattern in content:
                        errors.append(
                            f"ORM violation: "
                            f"Pydantic model '{name}' "
                            f"used in SQLAlchemy query "
                            f"in {os.path.relpath(file_path, project_path)}"
                        )
                        break