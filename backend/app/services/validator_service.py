import os
import re
import py_compile
import ast


def validate_backend_imports(project_path, errors):

    for root, dirs, files in os.walk(project_path):

        for file in files:

            if not file.endswith(".py"):
                continue

            file_path = os.path.join(
                root,
                file
            )

            try:

                with open(
                    file_path,
                    "r",
                    encoding="utf-8"
                ) as f:

                    content = f.read()

            except Exception:
                continue

            imports = re.findall(
                r"from\s+app\.(services|models|routes)\.(\w+)\s+import",
                content
            )

            for folder, module in imports:

                expected = os.path.join(
                    project_path,
                    "app",
                    folder,
                    f"{module}.py"
                )

                if not os.path.exists(expected):

                    errors.append(
                        f"Missing backend import target: {folder}/{module}.py"
                    )
def validate_imported_symbols(
    project_path,
    errors
):

    for root, dirs, files in os.walk(project_path):

        for file in files:

            if not file.endswith(".py"):
                continue

            file_path = os.path.join(
                root,
                file
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

            for node in ast.walk(tree):

                if not isinstance(
                    node,
                    ast.ImportFrom
                ):
                    continue

                if not node.module:
                    continue

                if not node.module.startswith(
                    "app."
                ):
                    continue

                module_path = (
                    node.module.replace(
                        ".",
                        os.sep
                    )
                    + ".py"
                )

                target_file = os.path.join(
                    project_path,
                    module_path
                )

                if not os.path.exists(
                    target_file
                ):
                    continue

                try:

                    with open(
                        target_file,
                        "r",
                        encoding="utf-8"
                    ) as f:

                        target_content = f.read()

                    target_tree = ast.parse(
                        target_content
                    )

                except Exception:
                    continue

                symbols = set()

                for item in target_tree.body:

                    if isinstance(
                        item,
                        ast.FunctionDef
                    ):
                        symbols.add(
                            item.name
                        )

                    elif isinstance(
                        item,
                        ast.AsyncFunctionDef
                    ):
                        symbols.add(
                            item.name
                        )

                    elif isinstance(
                        item,
                        ast.ClassDef
                    ):
                        symbols.add(
                            item.name
                        )

                    elif isinstance(
                        item,
                        ast.Assign
                    ):

                        for target in item.targets:

                            if isinstance(
                                target,
                                ast.Name
                            ):
                                symbols.add(
                                    target.id
                                )

                for alias in node.names:

                    if alias.name == "*":
                        continue

                    if (
                        alias.name
                        not in symbols
                    ):

                        errors.append(
                            f"Missing symbol '{alias.name}' in {module_path}"
                        )
def validate_frontend_imports(project_path, errors):

    src_path = os.path.join(
        project_path,
        "src"
    )

    if not os.path.exists(src_path):
        return

    for root, dirs, files in os.walk(src_path):

        for file in files:

            if not file.endswith(".jsx"):
                continue

            file_path = os.path.join(
                root,
                file
            )

            try:

                with open(
                    file_path,
                    "r",
                    encoding="utf-8"
                ) as f:

                    content = f.read()

            except Exception:
                continue

            imports = re.findall(
                r'import\s+.*?\s+from\s+[\'"](.+?)[\'"]',
                content
            )

            for imp in imports:

                if not imp.startswith("."):
                    continue

                possible_paths = []

                possible_paths.append(
                    os.path.normpath(
                        os.path.join(
                            root,
                            imp + ".jsx"
                        )
                    )
                )

                filename = os.path.basename(imp)

                possible_paths.append(
                    os.path.join(
                        src_path,
                        "pages",
                        f"{filename}.jsx"
                    )
                )

                possible_paths.append(
                    os.path.join(
                        src_path,
                        "components",
                        f"{filename}.jsx"
                    )
                )
                possible_paths.append(
                         os.path.join(
                            src_path,
                                filename,
                            "index.jsx"
                             )
                            )

                exists = any(
                    os.path.exists(path)
                    for path in possible_paths
                )

                if not exists:

                    errors.append(
                        f"Missing frontend import target: {imp}.jsx"
                    )
def validate_route_quality(
    project_path,
    errors
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

        try:

            with open(
                file_path,
                "r",
                encoding="utf-8"
            ) as f:

                content = f.read()

        except Exception:
            continue

        if "APIRouter(" not in content:

            errors.append(
                f"Missing APIRouter in routes/{file}"
            )

        route_count = len(
            re.findall(
                r"@\w+_router\.(get|post|put|patch|delete)",
                content
            )
        )

        if route_count == 0:

            errors.append(
                f"No endpoints found in routes/{file}"
            )
def validate_requirements(
    project_path,
    errors
):

    requirements_file = os.path.join(
        project_path,
        "app",
        "requirements.txt"
    )

    if not os.path.exists(
        requirements_file
    ):
        return

    with open(
        requirements_file,
        "r",
        encoding="utf-8"
    ) as f:

        requirements = {
            line.strip()
            for line in f.readlines()
            if line.strip()
        }

    known_packages = {
        "fastapi",
        "uvicorn",
        "pydantic",
        "email-validator",
        "python-multipart",
        "requests"
    }

    for package in requirements:

        if package not in known_packages:

            errors.append(
                f"Unknown dependency: {package}"
            )
def validate_fastapi_routes(
    project_path,
    errors
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

        file_path = os.path.join(
            routes_dir,
            file
        )

        try:

            with open(
                file_path,
                "r",
                encoding="utf-8"
            ) as f:

                content = f.read()

        except Exception:
            continue

        if file == "__init__.py":
            continue

        if "APIRouter" not in content:
            errors.append(
        f"Missing APIRouter in {relative_path}"
        )

def validate_project(project_path):

    errors = []

    main_file = os.path.join(
        project_path,
        "app",
        "main.py"
    )

    if not os.path.exists(main_file):

        errors.append(
            "Missing app/main.py"
        )

        return {
            "passed": False,
            "errors": errors
        }

    with open(
        main_file,
        "r",
        encoding="utf-8"
    ) as f:

        content = f.read()

    imports = re.findall(
        r"from\s+app\.routes\.(\w+)\s+import",
        content
    )

    for route_name in imports:

        route_file = os.path.join(
            project_path,
            "app",
            "routes",
            f"{route_name}.py"
        )

        if not os.path.exists(route_file):

            errors.append(
                f"Missing route file: {route_name}.py"
            )

    validate_backend_imports(
        project_path,
        errors
    )
    validate_imported_symbols(
    project_path,
    errors
)

    validate_frontend_imports(
        project_path,
        errors
    )
    validate_route_quality(
    project_path,
    errors
)
    validate_requirements(
    project_path,
    errors
)
    
    validate_fastapi_routes(
    project_path,
    errors
)

    for root, dirs, files in os.walk(project_path):

        for file in files:

            if not file.endswith(".py"):
                continue

            file_path = os.path.join(
                root,
                file
            )

            try:

                py_compile.compile(
                    file_path,
                    doraise=True
                )

            except Exception as e:

                relative_path = os.path.relpath(
                    file_path,
                    project_path
                )

                errors.append(
                    f"Syntax error in {relative_path}: {str(e)}"
                )

    return {
        "passed": len(errors) == 0,
        "errors": errors
    }