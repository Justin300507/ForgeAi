import os
import re
import py_compile
import ast
from app.services.undefined_symbol_validator import (
    validate_undefined_symbols
)
from app.services.architecture_validator import (
    validate_architecture,
    load_metadata
)
from app.services.orm_validator import (
    validate_orm_usage,
    validate_no_flask_sqlalchemy
)
from app.services.endpoint_validator import (
    validate_endpoints,
    validate_orphan_routes
)
from app.services.database_validator import (
    validate_database
)
from app.services.router_export_validator import (
    validate_router_exports
)
from app.services.schema_model_validator import (
    validate_schema_model_consistency
)
from app.services.session_validator import (
    validate_session_management
)
from app.services.self_shadow_validator import (
    validate_self_shadowing_functions
)
from app.services.stub_handler_validator import validate_stub_handlers
from app.services.global_statement_validator import validate_module_level_global
from app.services.duplicate_class_validator import validate_duplicate_class_definitions


def rel(project_path, file_path):
    """Canonical relative path with forward slashes, used by every
    validator below so error messages always reference files the
    same way (no more app\\routes\\x.py vs routes/x.py vs x.py)."""
    return os.path.relpath(file_path, project_path).replace(os.sep, "/")


def validate_backend_imports(project_path, errors):
    _SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv", "dist"}
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]

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

            # Generalized over any depth ("app.config", "app.services.foo",
            # "app.services.foo.bar") instead of a fixed folder whitelist — a
            # single-segment top-level import like "from app.config import
            # settings" previously matched no whitelisted folder and silently
            # bypassed every static check, only surfacing as a runtime
            # ModuleNotFoundError.
            imports = re.findall(
                r"^from\s+app\.([\w.]+)\s+import",
                content,
                re.MULTILINE,
            )

            for dotted in imports:

                segments = dotted.split(".")

                expected = os.path.join(
                    project_path,
                    "app",
                    *segments[:-1],
                    f"{segments[-1]}.py"
                )

                if os.path.exists(expected):
                    continue

                package_init = os.path.join(
                    project_path, "app", *segments, "__init__.py"
                )

                if os.path.exists(package_init):
                    continue

                errors.append(
    f"Missing backend import target: app/{'/'.join(segments)}.py"
)

def validate_imported_symbols(
    project_path,
    errors
):
    _SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv", "dist"}
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]

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

                is_package = False
                package_dir = None

                if not os.path.exists(target_file):

                    package_dir = os.path.join(
                        project_path,
                        node.module.replace(".", os.sep)
                    )
                    package_init = os.path.join(package_dir, "__init__.py")

                    if os.path.exists(package_init):
                        target_file = package_init
                        is_package = True
                    else:
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

                    elif isinstance(item, ast.ImportFrom):

                        for alias in item.names:
                            symbols.add(alias.asname or alias.name)

                    elif isinstance(item, ast.Import):

                        for alias in item.names:
                            symbols.add(
                                alias.asname or alias.name.split(".")[0]
                            )

                for alias in node.names:

                    if alias.name == "*":
                        continue

                    if alias.name in symbols:
                        continue

                    if is_package:
                        submodule_path = os.path.join(
                            package_dir, f"{alias.name}.py"
                        )
                        if os.path.exists(submodule_path):
                            continue

                    errors.append(
                        f"Missing symbol '{alias.name}' in {rel(project_path, target_file)} "
                        f"(imported from {rel(project_path, file_path)})"
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

                if imp.endswith(".jsx"):

                    possible_paths.append(
                        os.path.normpath(
                            os.path.join(
                                root,
                                imp
                            )
                        )
                    )

                elif imp.endswith(".js"):

                    possible_paths.append(
                        os.path.normpath(
                            os.path.join(
                                root,
                                imp
                            )
                        )
                    )

                else:

                    possible_paths.append(
                        os.path.normpath(
                            os.path.join(
                                root,
                                imp + ".jsx"
                            )
                        )
                    )
                    possible_paths.append(
                        os.path.normpath(
                            os.path.join(
                                root,
                                imp + ".js"
                            )
                        )
                    )

                filename = os.path.basename(
                    imp.replace(".jsx", "").replace(".js", "")
                )

                # An import like "./contexts/AuthContext" specifies an exact
                # subdirectory — a real bundler resolves it literally, with no
                # fuzzy fallback. Only fall back to guessing pages/components/
                # utils/src-root locations for flat imports (e.g. "./AuthContext")
                # where the LLM may have put the file in a conventional folder
                # without matching the import path exactly.
                _imp_rel = imp
                while _imp_rel.startswith("../") or _imp_rel.startswith("./"):
                    _imp_rel = _imp_rel[3:] if _imp_rel.startswith("../") else _imp_rel[2:]
                has_explicit_subdir = "/" in _imp_rel

                if not has_explicit_subdir:

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

                    # Check src/utils/ — JS utilities provided by templates
                    possible_paths.append(
                        os.path.join(src_path, "utils", f"{filename}.js")
                    )
                    possible_paths.append(
                        os.path.join(src_path, "utils", f"{filename}.jsx")
                    )
                    # Check src-root for files generated at the top level
                    possible_paths.append(os.path.join(src_path, f"{filename}.jsx"))
                    possible_paths.append(os.path.join(src_path, f"{filename}.js"))

                exists = any(
                    os.path.exists(path)
                    for path in possible_paths
                )

                if not exists:

                    errors.append(
                        f"Missing frontend import target: {imp}"
                    )

                else:

                    resolved_path = next(
                        (p for p in possible_paths if os.path.exists(p)),
                        None
                    )

                    if resolved_path:

                        try:
                            with open(
                                resolved_path, "r", encoding="utf-8"
                            ) as tf:
                                target_content = tf.read()
                        except Exception:
                            target_content = ""

                        has_default_export = "export default" in target_content

                        specific_import_match = re.search(
                            r'import\s+(\{[^}]*\}|\S+)\s+from\s+[\'"]'
                            + re.escape(imp) + r'[\'"]',
                            content
                        )

                        is_named_import_for_this_target = bool(
                            specific_import_match
                            and specific_import_match.group(1).startswith("{")
                        )

                        if has_default_export and is_named_import_for_this_target:

                            errors.append(
                                f"Import style mismatch: {imp} uses a default "
                                f"export but is imported with named-import "
                                f"syntax (curly braces) in "
                                f"{os.path.relpath(file_path, project_path)}"
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
                f"Missing APIRouter in {rel(project_path, file_path)}"
            )

        route_count = len(
            re.findall(
                r"@\w+\.(get|post|put|patch|delete)",
                content
            )
        )

        if route_count == 0:

            errors.append(
                f"No endpoints found in {rel(project_path, file_path)}"
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

        errors.append(
    "Missing requirements.txt (app/requirements.txt)"
)
        return

    try:

        with open(
            requirements_file,
            "r",
            encoding="utf-8"
        ) as f:

            content = f.read().strip()

        if not content:

            errors.append(
    "requirements.txt is empty (app/requirements.txt)"
)

    except Exception as e:

        errors.append(
            f"Could not read requirements.txt: {str(e)}"
        )


def validate_common_antipatterns(project_path, errors):
    """Catch LLM anti-patterns that validators miss but always crash at runtime."""
    routes_dir = os.path.join(project_path, "app", "routes")
    if not os.path.isdir(routes_dir):
        return

    for fname in os.listdir(routes_dir):
        if not fname.endswith(".py"):
            continue
        fpath = os.path.join(routes_dir, fname)
        try:
            content = open(fpath, encoding="utf-8").read()
        except Exception:
            continue

        rel_path = rel(project_path, fpath)

        # auth2_scheme typo — crashes FastAPI at startup
        if "auth2_scheme" in content and "oauth2_scheme" not in content:
            errors.append(
                f"Typo 'auth2_scheme' in {rel_path} — must be 'oauth2_scheme'. "
                "Define: oauth2_scheme = OAuth2PasswordBearer(tokenUrl='/auth/token')"
            )

        # werkzeug import — Flask lib, not installed
        if "from werkzeug" in content or "import werkzeug" in content:
            errors.append(
                f"werkzeug imported in {rel_path} — Flask library, not available. "
                "Use passlib: from passlib.context import CryptContext"
            )

        # orm_mode — Pydantic v1 deprecated in v2
        if "orm_mode = True" in content:
            errors.append(
                f"orm_mode=True in {rel_path} — use from_attributes=True (Pydantic v2)"
            )

    # Check schema files for datetime fields typed as str (causes ResponseValidationError)
    schemas_dir = os.path.join(project_path, "app", "schemas")
    if os.path.isdir(schemas_dir):
        _DT_FIELDS_RE = re.compile(
            r'^\s+(created_at|updated_at|created|updated|timestamp\w*)\s*:\s*str\b',
            re.MULTILINE
        )
        for fname in os.listdir(schemas_dir):
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(schemas_dir, fname)
            try:
                content = open(fpath, encoding="utf-8").read()
            except Exception:
                continue
            m = _DT_FIELDS_RE.search(content)
            if m:
                rel_path = rel(project_path, fpath)
                errors.append(
                    f"Schema datetime typed as str in {rel_path}: "
                    f"'{m.group().strip()}' — use 'from datetime import datetime' "
                    "and declare the field as 'datetime', not 'str'. "
                    "Also ensure model_config = ConfigDict(from_attributes=True)."
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
                f"Missing route file: app/routes/{route_name}.py"
            )

    validate_backend_imports(
        project_path,
        errors
    )
    validate_imported_symbols(
        project_path,
        errors
    )
    validate_router_exports(
        project_path,
        errors
    )
    validate_database(
        project_path,
        errors
    )
    metadata = load_metadata(
        project_path
    )
    validate_architecture(
        project_path,
        metadata,
        errors
    )
    validate_endpoints(
        project_path,
        metadata,
        errors
    )
    validate_orphan_routes(
        project_path,
        metadata,
        errors
    )
    validate_undefined_symbols(
        project_path,
        errors
    )
    validate_self_shadowing_functions(
    project_path,
    errors
)
    validate_orm_usage(
        project_path,
        errors
    )
    validate_no_flask_sqlalchemy(
        project_path,
        errors
    )
    validate_session_management(
        project_path,
        errors
    )
    validate_schema_model_consistency(
        project_path,
        errors
    )

    validate_frontend_imports(
        project_path,
        errors
    )
    validate_stub_handlers(
    project_path,
    errors
)
    validate_module_level_global(
    project_path,
    errors
)
    validate_duplicate_class_definitions(
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
    validate_common_antipatterns(
        project_path,
        errors
    )

    _SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv", "dist", ".next"}
    for root, dirs, files in os.walk(project_path):
        # Prune dirs in-place to skip non-project directories
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]

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
        "errors": list(dict.fromkeys(errors))
    }