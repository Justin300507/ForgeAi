import ast
import os


def build_symbol_table(project_root):
    """
    Builds a symbol table for all Python files.

    Example:

    {
        "app.models.task": {"Task"},
        "app.routes.task_routes": {"task_router", "get_tasks"}
    }
    """

    symbol_table = {}

    for root, _, files in os.walk(project_root):

        for file in files:

            if not file.endswith(".py"):
                continue

            path = os.path.join(root, file)

            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()

                tree = ast.parse(content)

                symbols = set()

                for node in ast.walk(tree):

                    # classes
                    if isinstance(node, ast.ClassDef):
                        symbols.add(node.name)

                    # functions
                    elif isinstance(node, ast.FunctionDef):
                        symbols.add(node.name)

                    # async functions
                    elif isinstance(node, ast.AsyncFunctionDef):
                        symbols.add(node.name)

                    # variables
                    elif isinstance(node, ast.Assign):

                        for target in node.targets:

                            if isinstance(target, ast.Name):
                                symbols.add(target.id)

                module_name = (
                    os.path.relpath(path, project_root)
                    .replace(os.sep, ".")
                    .replace(".py", "")
                )

                symbol_table[module_name] = symbols

            except Exception:
                pass

    return symbol_table


def validate_imports(imports, symbol_table):
    """
    Checks imported symbols actually exist.
    """

    errors = []

    for item in imports:

        module = item["module"]
        symbol = item["symbol"]

        if not module:
            continue

        if module not in symbol_table:

            errors.append({
                "type": "missing_module",
                "module": module,
                "symbol": symbol,
                "file": item["file"]
            })

            continue

        if symbol not in symbol_table[module]:

            errors.append({
                "type": "missing_symbol",
                "module": module,
                "symbol": symbol,
                "file": item["file"]
            })

    return errors