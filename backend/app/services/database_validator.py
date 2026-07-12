import ast
import os

from app.core.context import Diagnostic, ErrorCategory, ErrorSeverity


def _add_db(errors, diagnostics, msg, rel_path):
    errors.append(msg)
    if diagnostics is not None:
        # category=CONTRACT, severity=MEDIUM: exact parity with engine.py's
        # existing "database" substring match (category) and default
        # (severity -- neither message matches a severity-specific pattern).
        diagnostics.append(Diagnostic(
            error_id=Diagnostic.make_id("static", ErrorCategory.CONTRACT, msg, rel_path),
            category=ErrorCategory.CONTRACT,
            severity=ErrorSeverity.MEDIUM,
            source="static",
            message=msg,
            file_path=rel_path,
            validator_name="validate_database",
        ))


def validate_database(project_path, errors, diagnostics=None):

    database_file = os.path.join(
        project_path,
        "app",
        "database.py"
    )

    if not os.path.exists(database_file):

        _add_db(errors, diagnostics, "Missing app/database.py", "app/database.py")

        return

    try:

        with open(
            database_file,
            "r",
            encoding="utf-8"
        ) as f:

            database_content = f.read()

        database_tree = ast.parse(
            database_content
        )

    except Exception:

        return

    exported = set()

    # Walk ALL nodes (not just top-level body) to catch assignments inside if/else blocks
    for node in ast.walk(database_tree):

        if isinstance(node, ast.Assign):

            for target in node.targets:

                if isinstance(target, ast.Name):
                    exported.add(target.id)

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):

            exported.add(node.name)

        elif isinstance(node, ast.ClassDef):

            exported.add(node.name)

        elif isinstance(node, ast.ImportFrom):

            for alias in node.names:
                exported.add(alias.asname or alias.name)

        elif isinstance(node, ast.Import):

            for alias in node.names:
                exported.add(
                    alias.asname or alias.name.split(".")[0]
                )

    for root, _, files in os.walk(project_path):

        for file in files:

            if not file.endswith(".py"):
                continue

            file_path = os.path.join(
                root,
                file
            )

            if file_path == database_file:
                continue

            try:

                with open(
                    file_path,
                    "r",
                    encoding="utf-8"
                ) as f:

                    content = f.read()

                tree = ast.parse(content)

            except Exception:
                continue

            for node in ast.walk(tree):

                if not isinstance(
                    node,
                    ast.ImportFrom
                ):
                    continue

                if node.module != "app.database":
                    continue

                for alias in node.names:

                    if alias.name not in exported:

                        rel_path = os.path.relpath(file_path, project_path).replace(os.sep, "/")
                        _add_db(errors, diagnostics,
                            f"Database symbol '{alias.name}' "
                            f"missing from app/database.py "
                            f"(imported from "
                            f"{os.path.relpath(file_path, project_path)})",
                            rel_path)