import ast
import os

from app.core.context import Diagnostic, ErrorCategory, ErrorSeverity


def validate_stub_handlers(project_path, errors, diagnostics=None):
    """Detect route handlers that are just `pass` (no real
    implementation) but declare a response_model — this WILL crash
    at request time with a ResponseValidationError, since FastAPI
    cannot serialize None against a schema. Catches it statically
    instead of discovering it via an actual runtime crash."""

    routes_dir = os.path.join(project_path, "app", "routes")

    if not os.path.exists(routes_dir):
        return

    for file in os.listdir(routes_dir):

        if not file.endswith(".py") or file == "__init__.py":
            continue

        file_path = os.path.join(routes_dir, file)

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            tree = ast.parse(content)
        except Exception:
            continue

        for node in ast.walk(tree):

            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            body = node.body

            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body = body[1:]

            # A docstring-only body (nothing left after stripping it) or an
            # Ellipsis (`...`) placeholder body fall through and return None
            # exactly like bare `pass` — all three crash identically against
            # a declared response_model.
            is_stub = (
                len(body) == 0
                or (len(body) == 1 and isinstance(body[0], ast.Pass))
                or (
                    len(body) == 1
                    and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and body[0].value.value is Ellipsis
                )
            )

            if not is_stub:
                continue

            has_response_model = False

            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call):
                    for kw in decorator.keywords:
                        if kw.arg == "response_model":
                            has_response_model = True

            if has_response_model:

                rel_path = os.path.relpath(file_path, project_path).replace(os.sep, "/")
                msg = (
                    f"Stub handler risk in "
                    f"{os.path.relpath(file_path, project_path)}: "
                    f"function '{node.name}' has an empty body (pass) "
                    f"but declares response_model — this will crash "
                    f"with a ResponseValidationError when called"
                )
                errors.append(msg)
                if diagnostics is not None:
                    # category=CONTRACT (via "stub" match), severity=MEDIUM
                    # (via "stub handler" match): exact parity with
                    # engine.py's existing heuristics.
                    diagnostics.append(Diagnostic(
                        error_id=Diagnostic.make_id("static", ErrorCategory.CONTRACT, msg, rel_path),
                        category=ErrorCategory.CONTRACT,
                        severity=ErrorSeverity.MEDIUM,
                        source="static",
                        message=msg,
                        file_path=rel_path,
                        validator_name="validate_stub_handlers",
                    ))