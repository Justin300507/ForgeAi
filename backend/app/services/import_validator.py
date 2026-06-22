import ast
import os


def collect_imports(project_root):
    imports = []

    for root, _, files in os.walk(project_root):

        for file in files:

            if not file.endswith(".py"):
                continue

            path = os.path.join(root, file)

            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()

                tree = ast.parse(content)

                for node in ast.walk(tree):

                    if isinstance(node, ast.ImportFrom):

                        module = node.module

                        for alias in node.names:

                            imports.append(
                                {
                                    "file": path,
                                    "module": module,
                                    "symbol": alias.name
                                }
                            )

            except Exception:
                pass

    return imports