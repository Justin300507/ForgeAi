# app/services/duplicate_class_validator.py

import ast
import os

from app.core.context import Diagnostic, ErrorCategory, ErrorSeverity


def validate_duplicate_class_definitions(project_path, errors, diagnostics=None):
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
            # Common base classes appear everywhere by design — skip them
            if class_name in ("Base", "Config", "Meta", "Settings"):
                continue

            # models/ vs schemas/ is intentional in FastAPI: SQLAlchemy model and
            # Pydantic schema can share a name (e.g. User). Skip this cross-layer pair.
            def _layer(p: str) -> str:
                if "app/models/" in p:
                    return "models"
                if "app/schemas/" in p:
                    return "schemas"
                return p

            layers = {_layer(loc) for loc in locations}
            if layers == {"models", "schemas"}:
                continue

            # The deterministic patcher always injects a known-good, self-contained
            # app/routes/auth_routes.py that defines its own inline request/response
            # classes (LoginRequest, SignupRequest, ...) and never imports them from
            # app/schemas/ — see _AUTH_DEFINED_CLASSES in deterministic_patcher.py.
            # An LLM-generated app/schemas/auth.py defining a class with the same
            # name is not a real conflict: neither file ever imports the other's
            # version.
            if "app/routes/auth_routes.py" in locations:
                from app.services.deterministic_patcher import AUTH_DEFINED_CLASSES
                if class_name in AUTH_DEFINED_CLASSES:
                    continue

            msg = (
                f"Duplicate class definition: '{class_name}' is defined "
                f"in multiple files ({', '.join(locations)}) — this "
                f"creates incompatible duplicate types"
            )
            errors.append(msg)
            if diagnostics is not None:
                # category=CONTRACT, severity=MEDIUM: exact parity with
                # engine.py's default classification. No single file_path --
                # the class spans multiple files by definition, so the full
                # location list goes in metadata instead of guessing one.
                diagnostics.append(Diagnostic(
                    error_id=Diagnostic.make_id("static", ErrorCategory.CONTRACT, msg, None),
                    category=ErrorCategory.CONTRACT,
                    severity=ErrorSeverity.MEDIUM,
                    source="static",
                    message=msg,
                    validator_name="validate_duplicate_class_definitions",
                    metadata={"locations": list(locations), "class_name": class_name},
                ))