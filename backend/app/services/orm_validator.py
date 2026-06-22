# app/services/orm_validator.py

import ast
import os


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