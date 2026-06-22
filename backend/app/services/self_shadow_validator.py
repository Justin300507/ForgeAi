import ast
import os


def validate_self_shadowing_functions(project_path, errors):
    """Detect a function definition that shadows an imported symbol
    of the same name, where the function body then calls that name —
    causing infinite self-recursion instead of calling the intended
    imported function.

    Example of what this catches:
        from app.services.user_service import get_user
        def get_user(user_id: int, ...):
            return get_user(db, user_id)   # calls itself, not the import
    """

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

            imported_names = set()

            for node in tree.body:

                if isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        if alias.name != "*":
                            imported_names.add(alias.asname or alias.name)

                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        imported_names.add(
                            alias.asname or alias.name.split(".")[0]
                        )

            for node in tree.body:

                if not isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef)
                ):
                    continue

                func_name = node.name

                if func_name not in imported_names:
                    continue

                calls_self = False

                for inner in ast.walk(node):
                    if (
                        isinstance(inner, ast.Call)
                        and isinstance(inner.func, ast.Name)
                        and inner.func.id == func_name
                    ):
                        calls_self = True
                        break

                if calls_self:

                    errors.append(
                        f"Self-shadowing recursion risk in "
                        f"{os.path.relpath(file_path, project_path)}: "
                        f"function '{func_name}' has the same name as "
                        f"an imported symbol and calls itself instead "
                        f"of the intended import"
                    )