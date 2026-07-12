# app/services/global_statement_validator.py

import ast
import os

from app.core.context import Diagnostic, ErrorCategory, ErrorSeverity


def validate_module_level_global(project_path, errors, diagnostics=None):
    """A `global X, Y` statement at module level (not inside a
    function) is always meaningless — it does not import or define
    anything. Models sometimes write this as a fake substitute for
    missing imports, leaving the real undefined-symbol problem
    unsolved. Flag it explicitly so the Fix Agent adds real imports."""

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

                if isinstance(node, ast.Global):

                    rel_path = os.path.relpath(file_path, project_path).replace(os.sep, "/")
                    msg = (
                        f"Invalid module-level 'global' statement in "
                        f"{os.path.relpath(file_path, project_path)}: "
                        f"naming {', '.join(node.names)} — this does not "
                        f"import or define them; add real imports instead"
                    )
                    errors.append(msg)
                    if diagnostics is not None:
                        # category=CONTRACT, severity=MEDIUM: exact parity
                        # with engine.py's default classification.
                        diagnostics.append(Diagnostic(
                            error_id=Diagnostic.make_id("static", ErrorCategory.CONTRACT, msg, rel_path),
                            category=ErrorCategory.CONTRACT,
                            severity=ErrorSeverity.MEDIUM,
                            source="static",
                            message=msg,
                            file_path=rel_path,
                            validator_name="validate_module_level_global",
                        ))