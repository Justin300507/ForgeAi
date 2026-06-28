import json
import os
import re

from app.prompts.runtime_fix_prompt import (
    build_runtime_fix_prompt
)

from app.providers.ai_provider import (
    generate_content
)
from app.utils.json_cleaner import extract_json


def _trim_runtime_error(runtime_error: dict) -> dict:
    """Trim the runtime_error dict before building the LLM prompt.

    The raw dict can be 20KB+ (full stderr with tracebacks repeated 6× by uvicorn
    worker restarts). We only need the parsed structure + a tail of stderr.
    Saves ~15,000 tokens per runtime-fix call.
    """
    return {
        "success": runtime_error.get("success"),
        "exit_code": runtime_error.get("exit_code"),
        "parsed_error": runtime_error.get("parsed_error", {}),
        "stderr": (runtime_error.get("stderr") or "")[-1500:],
        "behavioral_issues": (runtime_error.get("behavioral_issues") or [])[:3],
    }


KNOWN_BAD_PACKAGE_NAMES = {
    "e-mail-validator": "email-validator",
    "eval-validator": "email-validator",
    "echo-validator": "email-validator",
    "pythonmultipart": "python-multipart",
    "fast-api": "fastapi",
}


def _fix_unresolvable_dependency(runtime_error, project_path):

    stderr = runtime_error.get("stderr", "") or ""

    match = re.search(
        r"Could not find a version that satisfies the requirement ([\w\-\.]+)",
        stderr
    )

    if not match:
        return None

    bad_package = match.group(1)
    bad_package_base = re.split(r"==|>=|<=", bad_package)[0]

    correct_package = KNOWN_BAD_PACKAGE_NAMES.get(bad_package_base)

    if not correct_package:
        return None

    requirements_path = os.path.join(
        project_path, "app", "requirements.txt"
    )

    try:
        with open(requirements_path, "r", encoding="utf-8") as f:
            requirements = f.read()
    except Exception:
        requirements = ""

    lines = [
        line for line in requirements.splitlines()
        if line.strip() and line.strip().split("==")[0] != bad_package_base
    ]

    if correct_package not in lines:
        lines.append(correct_package)

    print(
        f"Fixing unresolvable dependency: "
        f"{bad_package_base} -> {correct_package}"
    )

    return {
        "path": "app/requirements.txt",
        "content": "\n".join(lines).strip()
    }


def generate_runtime_fix(
    runtime_error,
    project_path,
    provider="auto",
):

    print(
        f"Runtime Fix Requested: {runtime_error}"
    )

    dependency_fix = _fix_unresolvable_dependency(runtime_error, project_path)
    if dependency_fix:
        return dependency_fix

    parsed_error = runtime_error.get(
        "parsed_error",
        {}
    )

    # ── postgres:// URL scheme / connection errors → overwrite database.py ──────
    error_type = parsed_error.get("type", "")
    stderr = runtime_error.get("stderr", "") or ""
    if error_type in ("PostgresURLSchemeError", "DatabaseConnectionError") or "postgres://" in stderr:
        try:
            from app.services.database_patcher import patch_database_py
            if patch_database_py(project_path):
                print("  db_patcher: rewrote app/database.py (postgres URL fix)")
                return None
        except Exception as dp_err:
            print(f"  db_patcher fix failed: {dp_err}")

    # ── SQLAlchemy mapper crash from back_populates → strip all of them ──
    error_type = parsed_error.get("type", "")
    stderr = runtime_error.get("stderr", "") or ""
    if error_type in ("SQLAlchemyError", "InvalidRequestError") or "back_populates" in stderr or "has no property" in stderr:
        try:
            from app.services.deterministic_patcher import _patch_strip_back_populates
            from pathlib import Path
            stripped = _patch_strip_back_populates(Path(project_path))
            if stripped:
                print(f"  back_populates fix: stripped from {stripped} model file(s) — mapper crash resolved")
                return None  # signal caller to re-run validation without LLM call
        except Exception as bp_err:
            print(f"  back_populates fix failed: {bp_err}")

    # ── SQLAlchemy relationship() string can't resolve model ─────────────────────
    # Python-level aliases (User = Users) do NOT work — SQLAlchemy's mapper registry
    # is keyed by cls.__name__, not by variable names.
    # Fix 1: patch the relationship() string in-place (deterministic, no LLM).
    # Fix 2: ensure the model file is imported in main.py so the mapper sees it.
    if parsed_error.get("type") == "RelationshipModelNotImported":
        missing_model = parsed_error.get("missing_model")
        if missing_model:
            main_py_path = os.path.join(project_path, "app", "main.py")
            try:
                # Fix 1: patch relationship strings so 'User' → 'Users' (actual class name)
                from app.services.deterministic_patcher import _patch_relationship_string_aliases
                from pathlib import Path
                _patch_relationship_string_aliases(Path(project_path))
                print(f"  RelationshipModelNotImported: patched relationship strings for '{missing_model}'")

                # Fix 2: find the actual class name (may differ from missing_model)
                module_name = missing_model.lower()
                model_file = os.path.join(project_path, "app", "models", f"{module_name}.py")
                actual_class = missing_model
                if os.path.exists(model_file):
                    src = open(model_file, encoding="utf-8").read()
                    classes = re.findall(r"^class (\w+)\(", src, re.MULTILINE)
                    if classes:
                        actual_class = classes[0]

                with open(main_py_path, "r", encoding="utf-8") as f:
                    main_content = f.read()
                import_line = f"from app.models.{module_name} import {actual_class}"
                if import_line not in main_content:
                    insert_after = re.search(r"(from app\.models\.\w+ import \w+\n)", main_content)
                    if insert_after:
                        pos = insert_after.end()
                        main_content = main_content[:pos] + import_line + "\n" + main_content[pos:]
                    else:
                        main_content = import_line + "\n" + main_content
                    print(f"  RelationshipModelNotImported: added `{import_line}` to main.py")
                    return {"path": "app/main.py", "content": main_content}
                else:
                    # Import already present — string fix alone should be sufficient
                    return None
            except Exception as e:
                print(f"  RelationshipModelNotImported fix failed: {e}")

    # ── Werkzeug: replace the entire auth_service with passlib ──────────
    if parsed_error.get("type") == "WerkzeugImportError":
        error_file = parsed_error.get("error_file", "")
        if error_file:
            try:
                relative_path = os.path.relpath(error_file, project_path)
                with open(error_file, "r", encoding="utf-8") as f:
                    content = f.read()
                # Replace werkzeug imports with bcrypt directly (passlib broken on Python 3.13+)
                _BCRYPT_HELPERS = (
                    "import bcrypt\n\n"
                    "def get_password_hash(password: str) -> str:\n"
                    "    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')\n\n"
                    "def verify_password(plain: str, hashed: str) -> bool:\n"
                    "    return bcrypt.checkpw(plain.encode('utf-8'), hashed.encode('utf-8'))\n\n"
                    "def generate_password_hash(password: str) -> str:\n"
                    "    return get_password_hash(password)\n\n"
                    "def check_password_hash(hashed: str, password: str) -> bool:\n"
                    "    return verify_password(password, hashed)\n"
                )
                for _old in (
                    "from werkzeug.security import generate_password_hash, check_password_hash",
                    "from werkzeug.security import check_password_hash, generate_password_hash",
                ):
                    content = content.replace(_old, _BCRYPT_HELPERS)
                # Ensure bcrypt in requirements, remove passlib
                req_path = os.path.join(project_path, "app", "requirements.txt")
                try:
                    with open(req_path, "r", encoding="utf-8") as f:
                        reqs = f.read()
                    reqs = "\n".join(l for l in reqs.splitlines() if not l.strip().startswith("passlib"))
                    if "bcrypt" not in reqs:
                        reqs += "\nbcrypt\n"
                    with open(req_path, "w", encoding="utf-8") as f:
                        f.write(reqs)
                except Exception:
                    pass
                print(f"Werkzeug fix: replaced with bcrypt in {relative_path}")
                return {"path": relative_path.replace(os.sep, "/"), "content": content}
            except Exception as e:
                print(f"Werkzeug auto-fix failed: {e}")

    module = parsed_error.get(
        "module",
        ""
    )

    # Missing internal app module (e.g. app.routes.auth_routes, app.schemas.user)
    # The LLM would try to "fix" main.py by removing the import — wrong.
    # Instead, generate the missing file from scratch.
    if (
        parsed_error.get("type") == "ModuleNotFoundError"
        and module
        and module.startswith("app.")
    ):
        missing_path = module.replace(".", os.sep) + ".py"
        full_missing_path = os.path.join(project_path, missing_path)
        if not os.path.exists(full_missing_path):
            print(f"Missing internal module {module} — generating file {missing_path}")
            try:
                from app.services.missing_file_service import generate_missing_file
                fix = generate_missing_file(missing_path, parsed_error, provider)
                if fix and fix.get("path") and fix.get("content"):
                    return fix
            except Exception as mfe:
                print(f"Missing file generation failed: {mfe}")

    if (
        parsed_error.get("type")
        == "ModuleNotFoundError"
        and module
        and not module.startswith("app.")
    ):

        requirements_path = os.path.join(
            project_path,
            "app",
            "requirements.txt"
        )

        try:

            with open(
                requirements_path,
                "r",
                encoding="utf-8"
            ) as f:

                requirements = f.read()

        except Exception:

            requirements = ""

        dependency_map = {
            "sqlalchemy": "sqlalchemy",
            "jwt": "PyJWT",
            "email_validator": "email-validator",
            "passlib": "passlib[bcrypt]",
            "bcrypt": "bcrypt",
            "pydantic": "pydantic",
            "fastapi": "fastapi",
            "uvicorn": "uvicorn",
            "werkzeug": "bcrypt",  # werkzeug is Flask-only; use bcrypt directly
        }

        dependency = dependency_map.get(
            module,
            module
        )

        if dependency not in requirements:

            requirements += (
                "\n"
                + dependency
            )

        print(
            f"Adding dependency: {dependency}"
        )

        return {
            "path": "app/requirements.txt",
            "content": requirements.strip()
        }

    # TimestampNotNullError: created_at/updated_at missing server_default
    if parsed_error.get("type") == "TimestampNotNullError":
        table = parsed_error.get("table", "")
        column = parsed_error.get("column", "")
        if table:
            models_dir = os.path.join(project_path, "app", "models")
            try:
                import glob as _glob, re as _re
                for mf in _glob.glob(os.path.join(models_dir, "*.py")):
                    content = open(mf, encoding="utf-8").read()
                    if f'__tablename__ = "{table}"' in content or f"__tablename__ = '{table}'" in content:
                        # Ensure func is imported
                        if "from sqlalchemy import" in content and "func" not in content:
                            content = content.replace(
                                "from sqlalchemy import",
                                "from sqlalchemy import func,",
                                1
                            )
                        elif "from sqlalchemy.sql import func" not in content and "from sqlalchemy import func" not in content:
                            content = "from sqlalchemy.sql import func\n" + content

                        # Fix created_at — add server_default if missing
                        content = _re.sub(
                            r"(created_at\s*=\s*Column\((?:DateTime|DATETIME)[^)]*?)(?:\s*,\s*nullable\s*=\s*False)?\s*\)",
                            r"created_at = Column(DateTime, server_default=func.now(), nullable=False)",
                            content
                        )
                        # Fix updated_at — add server_default + onupdate if missing
                        content = _re.sub(
                            r"(updated_at\s*=\s*Column\((?:DateTime|DATETIME)[^)]*?)(?:\s*,\s*nullable\s*=\s*False)?\s*\)",
                            r"updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)",
                            content
                        )
                        rel_path = os.path.relpath(mf, project_path).replace(os.sep, "/")
                        print(f"TimestampNotNullError fix: patching {rel_path}")
                        return {"path": rel_path, "content": content}
            except Exception as tse:
                print(f"TimestampNotNullError auto-fix failed: {tse}")

    # NoReferencedTableError: FK references a table that has no model file.
    # Strategy: strip the dangling FK from the model column (deterministic, no LLM).
    if parsed_error.get("type") == "NoReferencedTableError":
        missing_table = parsed_error.get("missing_table", "")
        if missing_table:
            try:
                from app.services.deterministic_patcher import _patch_dangling_foreign_keys
                from pathlib import Path
                stripped = _patch_dangling_foreign_keys(Path(project_path))
                if stripped:
                    print(f"NoReferencedTableError: stripped dangling FK(s) to '{missing_table}' ({stripped} file(s))")
                    # Return a sentinel so the caller re-runs validation without an LLM call
                    # We return None here; the caller will re-run runtime validation
                    return None
            except Exception as nrte:
                print(f"NoReferencedTableError dangling-FK fix failed: {nrte}")

    relative_path = ""
    absolute_path = ""
    file_content = ""

    error_file = parsed_error.get("error_file")

    if error_file:

        try:
            relative_path = os.path.relpath(error_file, project_path)
        except Exception:
            relative_path = error_file

    else:

        source_module = (
            parsed_error.get("import_target_module")
            or parsed_error.get("module")
        )

        if source_module:
            relative_path = source_module.replace(".", os.sep) + ".py"

    if relative_path:

        absolute_path = os.path.join(
            project_path,
            relative_path
        )

        try:

            with open(
                absolute_path,
                "r",
                encoding="utf-8"
            ) as f:

                file_content = f.read()

        except Exception as e:

            print(
                f"Failed to read runtime target file: {e}"
            )

            file_content = ""

    prompt = build_runtime_fix_prompt(
        _trim_runtime_error(runtime_error),
        relative_path,
        file_content
    )

    from app.utils.llm_cache import get_cached, set_cached
    _cache_payload = {"prompt": prompt}
    _cached = get_cached("runtime_fix", _cache_payload)
    if _cached is not None:
        print(f"      [runtime_fix cache hit] {relative_path}")
        return _cached

    text = generate_content(
        prompt,
        provider,
        max_tokens=8000
    )

    text = text.replace(
        "```json",
        ""
    )

    text = text.replace(
        "```",
        ""
    )

    text = text.strip()

    try:

        fix = extract_json(
            text
        )

        required_fields = [
            "path",
            "content"
        ]

        missing = [
            field
            for field in required_fields
            if field not in fix
        ]

        if missing:

            print(
                f"Runtime Fix Missing Fields: {missing}"
            )

            return None

        if not isinstance(
            fix["path"],
            str
        ):

            return None

        if not isinstance(
            fix["content"],
            str
        ):

            return None

        # Block path traversal before caching
        fix_path = fix.get("path", "")
        import os as _os
        norm = _os.path.normpath(fix_path)
        if norm.startswith("..") or _os.path.isabs(norm):
            print(f"  [runtime_fix] blocked path traversal: {fix_path!r}")
            return None

        set_cached("runtime_fix", _cache_payload, fix)
        return fix

    except json.JSONDecodeError as e:

        print(
            "Runtime Fix JSON Error:"
        )

        print(e)

        print(text)

        return None