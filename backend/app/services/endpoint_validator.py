import ast
import os
import re


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


def extract_actual_backend_routes(project_path):
    """AST-scan every app/routes/*.py file for @router.<method>("<path>")
    decorators (expanding any APIRouter(prefix=...)) and return the set of
    (METHOD, normalized_path) tuples that are actually implemented.

    Factored out of validate_endpoints so validate_frontend_api_calls can
    compare against the same ground truth without re-implementing the AST
    walk — what the backend actually serves shouldn't have two different
    definitions in the same validator module.
    """
    actual = set()

    routes_dir = os.path.join(project_path, "app", "routes")
    if not os.path.exists(routes_dir):
        return actual

    for root, _, files in os.walk(routes_dir):
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
                if not isinstance(node, ast.FunctionDef):
                    continue

                for decorator in node.decorator_list:
                    if not isinstance(decorator, ast.Call):
                        continue
                    if not isinstance(decorator.func, ast.Attribute):
                        continue

                    method = decorator.func.attr.upper()

                    if not decorator.args or not isinstance(decorator.args[0], ast.Constant):
                        continue

                    path = decorator.args[0].value

                    # Expand prefixed path: prefix="/items", path="/{id}" -> "/items/{id}"
                    if router_prefix:
                        rel = path.lstrip("/")
                        full_path = router_prefix + ("/" + rel if rel else "")
                    else:
                        full_path = path

                    actual.add((method, _normalize_path(full_path)))

    return actual


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

    actual = extract_actual_backend_routes(project_path)
    if not actual and not os.path.exists(os.path.join(project_path, "app", "routes")):
        return

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


_API_IMPORT_RE = re.compile(r"import\s+(\w+)\s+from\s+['\"][./]*api['\"]")
_METHOD_MAP = {"get": "GET", "post": "POST", "put": "PUT", "patch": "PATCH", "delete": "DELETE"}


def validate_frontend_api_calls(project_path, errors):
    """Cross-check every `API.<verb>(...)` call the frontend actually makes
    against the backend routes that actually exist.

    validate_endpoints only compares the architecture PLAN against the
    backend — an endpoint the frontend calls that was never in that plan (or
    was planned under a different path) is structurally invisible to it. In
    a real run this missed /reports/weekly, /stats/daily-completions,
    /stats/habit-completion-rates, /users/me/badges, /users/me/password, and
    DELETE /users/me — every one of them a page that loads forever or a
    button that silently 404s, none of it caught before deploy.
    """
    src_dir = os.path.join(project_path, "src")
    if not os.path.exists(src_dir):
        return

    actual = extract_actual_backend_routes(project_path)
    if not actual:
        return  # no backend routes parsed at all — nothing reliable to compare against

    call_re = re.compile(r"\.(get|post|put|patch|delete)\(\s*[`'\"]([^`'\"]+)[`'\"]")

    found: dict = {}  # (METHOD, normalized_path) -> (original_path, relpath)
    for root, _dirs, files in os.walk(src_dir):
        for file in files:
            if not file.endswith((".jsx", ".js")):
                continue
            file_path = os.path.join(root, file)
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception:
                continue

            if not _API_IMPORT_RE.search(content) and "API." not in content:
                continue  # this file doesn't use the shared axios client at all

            for m in call_re.finditer(content):
                method = _METHOD_MAP[m.group(1)]
                raw_path = m.group(2)
                if not raw_path.startswith("/"):
                    continue  # not a path (e.g. a relative asset URL) — skip rather than guess
                path = raw_path.split("?")[0]
                path = re.sub(r"\$\{[^}]*\}", "{}", path)  # template-literal interpolation -> {}
                norm = _normalize_path(path)
                key = (method, norm)
                if key not in found:
                    found[key] = (raw_path, os.path.relpath(file_path, project_path).replace("\\", "/"))

    missing = set(found.keys()) - actual
    for method, norm_path in sorted(missing):
        original_path, relpath = found[(method, norm_path)]
        # Derive resource/filename from the query-string-free path. original_path
        # (used below in the message, unchanged) can be
        # "/tasks?limit=5&sort_by=created_at&sort_order=desc" — deriving the
        # filename from THAT produced "tasks?limit=5&sort_by=..._routes.py",
        # an un-importable module the fix loop then dutifully created,
        # crashing every subsequent compile check (observed live 2026-07-06,
        # todo canary run, forge score 76.9 -> 25.5).
        resource = original_path.split("?")[0].strip("/").split("/")[0].rstrip("s") or "misc"
        expected_file = f"app/routes/{resource}_routes.py"
        # Same "Missing endpoint ... (expected in ...)" shape validate_endpoints
        # uses, so this flows through the existing fix-loop file-attribution
        # and ARCHITECTURE_ERROR_MARKERS dispatch without new wiring — the
        # loop already knows how to turn that message into a real route.
        errors.append(
            f"Missing endpoint {method} {original_path} (expected in {expected_file}) "
            f"-- called from {relpath} but never implemented on the backend"
        )


# app/services/endpoint_validator.py — replace validate_orphan_routes with this

def validate_orphan_routes(project_path, metadata, errors):
    routes_dir = os.path.join(project_path, "app", "routes")
    if not os.path.exists(routes_dir):
        return

    # Read main.py to find which route files are actually imported
    main_py_path = os.path.join(project_path, "app", "main.py")
    main_content = ""
    if os.path.exists(main_py_path):
        try:
            with open(main_py_path, encoding="utf-8") as f:
                main_content = f.read()
        except Exception:
            pass

    for file in os.listdir(routes_dir):
        if not file.endswith("_routes.py"):
            continue

        # Only flag files that main.py never imports — those are truly dead
        module_name = file.replace(".py", "")
        if module_name not in main_content and file not in main_content:
            errors.append(
                f"Orphan file: app/routes/{file} is not imported by main.py"
            )