import ast
import os


def validate_router_exports(
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

        for node in ast.walk(tree):

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

            errors.append(
                f"Router export mismatch "
                f"in app/routes/{file}. "
                f"Expected "
                f"'{expected_router}'"
            )