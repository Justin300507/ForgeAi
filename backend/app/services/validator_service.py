import os
import re
import py_compile


def validate_backend_imports(project_path, errors):

    for root, dirs, files in os.walk(project_path):

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

            except Exception:
                continue

            imports = re.findall(
                r"from\s+(services|models|routes)\.(\w+)\s+import",
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

            file_path = os.path.join(root, file)

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

                expected = os.path.normpath(
                    os.path.join(
                        root,
                        imp + ".jsx"
                    )
                )

                if not os.path.exists(expected):

                    errors.append(
                        f"Missing frontend import target: {imp}.jsx"
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
        r"from\s+routes\.(\w+)\s+import",
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

    # Backend Import Validation
    validate_backend_imports(
        project_path,
        errors
    )

    # Frontend Import Validation
    validate_frontend_imports(
        project_path,
        errors
    )

    # Python Syntax Validation
    for root, dirs, files in os.walk(project_path):

        for file in files:

            if file.endswith(".py"):

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

                    errors.append(
                        f"Syntax error in {file}: {str(e)}"
                    )

    return {
        "passed": len(errors) == 0,
        "errors": errors
    }
