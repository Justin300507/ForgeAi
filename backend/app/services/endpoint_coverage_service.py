import os
import re


def normalize_path(path: str) -> str:
    path = path.rstrip("/")

    path = re.sub(
        r"\{[^}]+\}",
        "{id}",
        path
    )

    return path


def calculate_endpoint_coverage(
    architecture,
    project_path
):
    required_endpoints = []

    for endpoint in architecture.get(
        "api_endpoints",
        []
    ):
        required_endpoints.append(
            (
                endpoint["method"].upper(),
                normalize_path(
                    endpoint["path"]
                )
            )
        )

    implemented_endpoints = set()

    routes_dir = os.path.join(
        project_path,
        "app",
        "routes"
    )

    if os.path.exists(routes_dir):

        pattern = re.compile(
            r"@\w+_router\.(get|post|put|delete|patch)\(\s*[\"']([^\"']+)[\"']"
        )

        for file in os.listdir(routes_dir):

            if not file.endswith(".py"):
                continue

            file_path = os.path.join(
                routes_dir,
                file
            )

            with open(
                file_path,
                "r",
                encoding="utf-8",
                errors="ignore"
            ) as f:

                content = f.read()

            matches = pattern.findall(
                content
            )

            for method, path in matches:

                implemented_endpoints.add(
                    (
                        method.upper(),
                        normalize_path(path)
                    )
                )

    implemented = 0

    for endpoint in required_endpoints:

        if endpoint in implemented_endpoints:
            implemented += 1

    total = len(required_endpoints)

    coverage = (
        round(
            (implemented / total) * 100,
            2
        )
        if total
        else 100
    )

    return {
        "required": total,
        "implemented": implemented,
        "coverage": coverage
    }