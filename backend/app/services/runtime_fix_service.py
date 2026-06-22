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

    # ── Werkzeug: replace the entire auth_service with passlib ──────────
    if parsed_error.get("type") == "WerkzeugImportError":
        error_file = parsed_error.get("error_file", "")
        if error_file:
            try:
                relative_path = os.path.relpath(error_file, project_path)
                with open(error_file, "r", encoding="utf-8") as f:
                    content = f.read()
                # Replace werkzeug imports with passlib equivalents
                content = content.replace(
                    "from werkzeug.security import generate_password_hash, check_password_hash",
                    "from passlib.context import CryptContext\n\n_pwd_context = CryptContext(schemes=[\"bcrypt\"], deprecated=\"auto\")\n\n\ndef generate_password_hash(password: str) -> str:\n    return _pwd_context.hash(password)\n\n\ndef check_password_hash(hashed: str, password: str) -> bool:\n    return _pwd_context.verify(password, hashed)"
                )
                content = content.replace(
                    "from werkzeug.security import check_password_hash, generate_password_hash",
                    "from passlib.context import CryptContext\n\n_pwd_context = CryptContext(schemes=[\"bcrypt\"], deprecated=\"auto\")\n\n\ndef generate_password_hash(password: str) -> str:\n    return _pwd_context.hash(password)\n\n\ndef check_password_hash(hashed: str, password: str) -> bool:\n    return _pwd_context.verify(password, hashed)"
                )
                # Add passlib to requirements
                req_path = os.path.join(project_path, "app", "requirements.txt")
                try:
                    with open(req_path, "r", encoding="utf-8") as f:
                        reqs = f.read()
                    if "passlib" not in reqs:
                        with open(req_path, "a", encoding="utf-8") as f:
                            f.write("\npasslib[bcrypt]\n")
                except Exception:
                    pass
                print(f"Werkzeug fix: replaced with passlib in {relative_path}")
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
            "werkzeug": "passlib[bcrypt]",  # werkzeug is Flask-only; use passlib
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

    # NoReferencedTableError: a model's table is FK-referenced but never imported/created
    if parsed_error.get("type") == "NoReferencedTableError":
        missing_table = parsed_error.get("missing_table", "")
        if missing_table:
            # Find the model file that defines this table
            models_dir = os.path.join(project_path, "app", "models")
            main_path = os.path.join(project_path, "app", "main.py")
            try:
                import glob as _glob
                model_files = _glob.glob(os.path.join(models_dir, "*.py"))
                matched_file = None
                for mf in model_files:
                    with open(mf, "r", encoding="utf-8") as f:
                        if f'__tablename__ = "{missing_table}"' in f.read() or f"__tablename__ = '{missing_table}'" in f.read():
                            matched_file = mf
                            break
                if matched_file:
                    # Get the model module name e.g. app.models.task
                    rel = os.path.relpath(matched_file, project_path).replace(os.sep, ".")[:-3]
                    model_name = os.path.basename(matched_file)[:-3].title().replace("_", "")
                    with open(main_path, "r", encoding="utf-8") as f:
                        main_content = f.read()
                    import_line = f"from {rel} import {model_name}  # noqa: F401 — ensures table is in metadata"
                    if rel not in main_content:
                        main_content = import_line + "\n" + main_content
                        print(f"NoReferencedTableError fix: adding {import_line}")
                        return {"path": "app/main.py", "content": main_content}
            except Exception as nrte:
                print(f"NoReferencedTableError auto-fix failed: {nrte}")

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
        runtime_error,
        relative_path,
        file_content
    )

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

        return fix

    except json.JSONDecodeError as e:

        print(
            "Runtime Fix JSON Error:"
        )

        print(e)

        print(text)

        return None