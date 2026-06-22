# app/services/duplicate_class_validator.py

import ast
import os


def validate_duplicate_class_definitions(project_path, errors):
    """A class (e.g. a SQLAlchemy model) should be defined in exactly
    one file. The same class name appearing in multiple files (e.g.
    Task in both models/task.py and services/task_service.py) creates
    incompatible duplicate types and signals a coherence breakdown
    during generation or repair."""

    class_locations = {}

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

            for node in tree.body:
                if isinstance(node, ast.ClassDef):
                    rel = os.path.relpath(
                        file_path, project_path
                    ).replace(os.sep, "/")
                    class_locations.setdefault(node.name, []).append(rel)

    for class_name, locations in class_locations.items():

        if len(locations) > 1:

            errors.append(
                f"Duplicate class definition: '{class_name}' is defined "
                f"in multiple files ({', '.join(locations)}) — this "
                f"creates incompatible duplicate types"
            )