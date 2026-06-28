import ast
import builtins
import os


def _collect_target_names(target, defined):
    """Recursively pull every Name out of an assignment/for/comprehension
    target, including tuple/list unpacking (a, b = ...) and starred
    targets (a, *rest = ...). Previously only plain ast.Name targets
    were handled, which caused false 'undefined symbol' errors on any
    tuple-unpacking loop or assignment."""

    if isinstance(target, ast.Name):
        defined.add(target.id)

    elif isinstance(target, (ast.Tuple, ast.List)):
        for elt in target.elts:
            _collect_target_names(elt, defined)

    elif isinstance(target, ast.Starred):
        _collect_target_names(target.value, defined)


def validate_undefined_symbols(project_path, errors):

    builtin_names = set(dir(builtins))

    for root, _, files in os.walk(project_path):

        for file in files:

            if not file.endswith(".py"):
                continue

            file_path = os.path.join(root, file)

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

            defined = set()
            used = set()

            for node in ast.walk(tree):

                if isinstance(node, ast.Import):

                    for alias in node.names:

                        defined.add(
                            alias.asname
                            or alias.name.split(".")[0]
                        )

                elif isinstance(node, ast.ImportFrom):

                    for alias in node.names:

                        defined.add(
                            alias.asname
                            or alias.name
                        )

                elif isinstance(node, ast.FunctionDef):

                    defined.add(node.name)

                    for arg in node.args.args:
                        defined.add(arg.arg)

                    for arg in node.args.kwonlyargs:
                        defined.add(arg.arg)

                    if node.args.vararg:
                        defined.add(node.args.vararg.arg)

                    if node.args.kwarg:
                        defined.add(node.args.kwarg.arg)

                elif isinstance(node, ast.AsyncFunctionDef):

                    defined.add(node.name)

                    for arg in node.args.args:
                        defined.add(arg.arg)

                    for arg in node.args.kwonlyargs:
                        defined.add(arg.arg)

                    if node.args.vararg:
                        defined.add(node.args.vararg.arg)

                    if node.args.kwarg:
                        defined.add(node.args.kwarg.arg)

                elif isinstance(node, ast.ClassDef):

                    defined.add(node.name)

                elif isinstance(node, ast.Assign):

                    for target in node.targets:
                        _collect_target_names(target, defined)

                elif isinstance(node, ast.AnnAssign):

                    if isinstance(node.target, ast.Name):
                        defined.add(node.target.id)

                elif isinstance(node, ast.For):

                    _collect_target_names(node.target, defined)

                elif isinstance(node, ast.comprehension):

                    _collect_target_names(node.target, defined)

                elif isinstance(node, ast.With):

                    for item in node.items:

                        if item.optional_vars:
                            _collect_target_names(
                                item.optional_vars, defined
                            )

            for node in ast.walk(tree):

                if isinstance(node, ast.Name):

                    if isinstance(node.ctx, ast.Load):
                        used.add(node.id)

            undefined = used - defined - builtin_names

            # Python module-level dunder attributes set by the import machinery
            _module_dunders = {
                "__file__", "__name__", "__doc__", "__package__",
                "__spec__", "__loader__", "__builtins__", "__cached__",
                "__all__", "__version__", "__author__",
            }
            ignored = {"self", "cls"} | _module_dunders
            undefined -= ignored

            for symbol in sorted(undefined):

                errors.append(
                    f"Undefined symbol '{symbol}' "
                    f"in {os.path.relpath(file_path, project_path)}"
                )