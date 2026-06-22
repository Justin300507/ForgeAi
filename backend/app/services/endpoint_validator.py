import ast
import os


def _normalize_path(path):
    """Collapse any {param_name} segment to {} so path comparison
    doesn't care what the param is actually called — /tasks/{id} and
    /tasks/{task_id} should be treated as the same endpoint."""

    segments = path.strip("/").split("/")
    normalized = [
        "{}" if seg.startswith("{") and seg.endswith("}") else seg
        for seg in segments
    ]
    return "/" + "/".join(normalized)


def validate_endpoints(
    project_path,
    metadata,
    errors
):

    architecture = metadata.get(
        "architecture",
        {}
    )

    expected = {}

    for endpoint in architecture.get(
        "api_endpoints",
        []
    ):

        method = endpoint.get(
            "method",
            ""
        ).upper()

        path = endpoint.get(
            "path",
            ""
        )

        # Strip query strings — ?tag={tag_id} is not a separate route,
        # it's handled by the same GET /tasks with an optional Query() param.
        path = path.split("?")[0]

        arch_file = (endpoint.get("file") or "").replace("\\", "/")

        expected[(method, _normalize_path(path))] = (path, arch_file)

    actual = set()

    routes_dir = os.path.join(
        project_path,
        "app",
        "routes"
    )

    if not os.path.exists(
        routes_dir
    ):
        return

    for root, _, files in os.walk(routes_dir):

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

                tree = ast.parse(content)

            except Exception:
                continue

            # Detect APIRouter(prefix="...") so prefixed routes are expanded
            router_prefix = ""
            for node in ast.walk(tree):
                if not isinstance(node, ast.Assign):
                    continue
                if not isinstance(node.value, ast.Call):
                    continue
                func = node.value.func
                if not (isinstance(func, ast.Name) and func.id == "APIRouter"):
                    continue
                for kw in node.value.keywords:
                    if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                        router_prefix = kw.value.value.rstrip("/")
                        break

            for node in ast.walk(tree):

                if not isinstance(
                    node,
                    ast.FunctionDef
                ):
                    continue

                for decorator in node.decorator_list:

                    if not isinstance(
                        decorator,
                        ast.Call
                    ):
                        continue

                    if not isinstance(
                        decorator.func,
                        ast.Attribute
                    ):
                        continue

                    method = (
                        decorator.func.attr
                        .upper()
                    )

                    if (
                        not decorator.args
                        or not isinstance(
                            decorator.args[0],
                            ast.Constant
                        )
                    ):
                        continue

                    path = (
                        decorator.args[0]
                        .value
                    )

                    # Expand prefixed path: prefix="/items", path="/{id}" -> "/items/{id}"
                    if router_prefix:
                        rel = path.lstrip("/")
                        full_path = router_prefix + ("/" + rel if rel else "")
                    else:
                        full_path = path

                    actual.add(
                        (method, _normalize_path(full_path))
                    )

    missing = set(expected.keys()) - actual

    for method, norm_path in sorted(missing):

        original_path, arch_file = expected[(method, norm_path)]

        if arch_file:
            expected_file = arch_file
        else:
            resource = original_path.strip("/").split("/")[0].rstrip("s")
            expected_file = f"app/routes/{resource}_routes.py"

        errors.append(
            f"Missing endpoint {method} {original_path} "
            f"(expected in {expected_file})"
        )


# app/services/endpoint_validator.py — replace validate_orphan_routes with this

def validate_orphan_routes(project_path, metadata, errors):

    architecture = metadata.get("architecture", {})

    expected_files = set()

    for endpoint in architecture.get("api_endpoints", []):
        file = endpoint.get("file")
        if file:
            expected_files.add(os.path.basename(file))

    routes_dir = os.path.join(project_path, "app", "routes")

    if not os.path.exists(routes_dir):
        return

    for file in os.listdir(routes_dir):

        if not file.endswith("_routes.py"):
            continue

        if file not in expected_files:

            errors.append(
                f"Orphan file: app/routes/{file} has no corresponding "
                f"architecture endpoint and should be removed"
            )