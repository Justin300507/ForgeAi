import json
import os

from app.prompts.runtime_fix_prompt import (
    build_runtime_fix_prompt
)

from app.providers.ai_provider import (
    generate_content
)
def generate_runtime_fix(
    runtime_error,
    project_path,
    provider="auto",
):

    print(
        f"Runtime Fix Requested: {runtime_error}"
    )

    parsed_error = runtime_error.get(
        "parsed_error",
        {}
    )

    module = parsed_error.get(
        "module",
        ""
    )

    if (
        parsed_error.get("type")
        ==
        "ModuleNotFoundError"
        and
        module
        and
        not module.startswith("app.")
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
            "uvicorn": "uvicorn"
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

    relative_path = ""
    absolute_path = ""
    file_content = ""

    source_module = parsed_error.get(
        "source_module"
    )

    if source_module:

        relative_path = (
            source_module.replace(
                ".",
                os.sep
            )
            + ".py"
        )

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
        max_tokens=2000
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

        fix = json.loads(
            text
        )

        return fix

    except json.JSONDecodeError as e:

        print(
            "Runtime Fix JSON Error:"
        )

        print(e)

        print(text)

        return None
