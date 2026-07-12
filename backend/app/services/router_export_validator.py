import ast
import os

from app.core.context import Diagnostic, ErrorCategory, ErrorSeverity


def validate_router_exports(
    project_path,
    errors,
    diagnostics=None
):

    routes_dir = os.path.join(
        project_path,
        "app",
        "routes"
    )

    if not os.path.exists(
        routes_dir
    ):
        return

    for file in os.listdir(
        routes_dir
    ):

        if not file.endswith(".py"):
            continue

        if file == "__init__.py":
            continue

        file_path = os.path.join(
            routes_dir,
            file
        )

        expected_router = (
            file.replace(
                "_routes.py",
                ""
            )
            + "_router"
        )

        try:

            with open(
                file_path,
                "r",
                encoding="utf-8"
            ) as f:

                content = f.read()

            tree = ast.parse(
                content
            )

        except Exception:
            continue

        found = False

        # Module-level only (tree.body) — a router variable assigned inside a
        # nested function is not a module-level export, so `from ... import
        # {expected_router}` would raise ImportError even though this check
        # (previously ast.walk, which descends into function bodies) considered
        # it "found".
        for node in tree.body:

            if isinstance(node, ast.Assign):

                for target in node.targets:

                    if (
                        isinstance(target, ast.Name)
                        and target.id == expected_router
                    ):
                        found = True

            elif isinstance(node, ast.AnnAssign):

                if (
                    isinstance(node.target, ast.Name)
                    and node.target.id == expected_router
                ):
                    found = True

        if not found:

            msg = (
                f"Router export mismatch "
                f"in app/routes/{file}. "
                f"Expected "
                f"'{expected_router}'"
            )
            errors.append(msg)
            if diagnostics is not None:
                # category=CONTRACT, severity=HIGH: exact parity with
                # engine.py's existing "router export" heuristic match.
                rel_path = f"app/routes/{file}"
                diagnostics.append(Diagnostic(
                    error_id=Diagnostic.make_id("static", ErrorCategory.CONTRACT, msg, rel_path),
                    category=ErrorCategory.CONTRACT,
                    severity=ErrorSeverity.HIGH,
                    source="static",
                    message=msg,
                    file_path=rel_path,
                    validator_name="validate_router_exports",
                ))