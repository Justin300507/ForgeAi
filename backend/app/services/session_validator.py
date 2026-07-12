# Add this check inside validate_session_management, in app/services/session_validator.py

import ast
import os

from app.core.context import Diagnostic, ErrorCategory, ErrorSeverity


def _add_session(errors, diagnostics, msg, rel_path):
    errors.append(msg)
    if diagnostics is not None:
        # category=CONTRACT (via "session" match), severity=MEDIUM (via
        # "session leak" match): exact parity with engine.py's heuristics.
        diagnostics.append(Diagnostic(
            error_id=Diagnostic.make_id("static", ErrorCategory.CONTRACT, msg, rel_path),
            category=ErrorCategory.CONTRACT,
            severity=ErrorSeverity.MEDIUM,
            source="static",
            message=msg,
            file_path=rel_path,
            validator_name="validate_session_management",
        ))


def validate_session_management(project_path, errors, diagnostics=None):

    for root, _, files in os.walk(project_path):

        for file in files:

            if not file.endswith(".py"):
                continue

            path = os.path.join(root, file)

            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                tree = ast.parse(content)
            except Exception:
                continue

            for node in ast.walk(tree):

                if not isinstance(
                    node,
                    (ast.FunctionDef, ast.AsyncFunctionDef)
                ):
                    continue

                func_source = ast.get_source_segment(content, node) or ""

                # Original check: direct SessionLocal() with no close
                if "SessionLocal()" in func_source:
                    # "with SessionLocal()" is the actual auto-closing context-manager
                    # pattern; a bare "with " anywhere in the function (e.g. "with
                    # open(...) as f:") is unrelated and previously suppressed real
                    # leaks whenever the function happened to contain any with-block.
                    has_close = (
                        ".close()" in func_source
                        or "with SessionLocal()" in func_source
                    )
                    if not has_close:
                        rel_path = os.path.relpath(path, project_path).replace(os.sep, "/")
                        _add_session(errors, diagnostics,
                            f"Session leak risk in "
                            f"{os.path.relpath(path, project_path)}: "
                            f"function '{node.name}' creates a session "
                            f"directly via SessionLocal() but never closes it",
                            rel_path)

                # New check: bare Depends() with no managed lifecycle
                for arg, default in zip(
                    node.args.args[-len(node.args.defaults):]
                    if node.args.defaults else [],
                    node.args.defaults
                ):
                    if not (
                        isinstance(default, ast.Call)
                        and isinstance(default.func, ast.Name)
                        and default.func.id == "Depends"
                        and len(default.args) == 0
                    ):
                        continue

                    if arg.annotation and (
                        getattr(arg.annotation, "id", "") == "Session"
                    ):
                        if ".close()" not in func_source:
                            rel_path = os.path.relpath(path, project_path).replace(os.sep, "/")
                            _add_session(errors, diagnostics,
                                f"Session leak risk in "
                                f"{os.path.relpath(path, project_path)}: "
                                f"function '{node.name}' uses bare Depends() "
                                f"for a Session parameter with no lifecycle "
                                f"management (no get_db generator, no close) — "
                                f"connections will accumulate per request",
                                rel_path)