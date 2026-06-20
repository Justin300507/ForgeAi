import json
import time

from fastapi import HTTPException

from app.prompts.backend_prompt import build_backend_prompt
from app.models.backend_models import BackendPlan
from app.providers.ai_provider import generate_content

def generate_backend(
architecture,
provider="auto",
max_tokens=1500
):

````
try:

    print("\n=== START BACKEND ===")

    start = time.time()

    prompt = build_backend_prompt(
        architecture
    )

    text = generate_content(
        prompt,
        provider,
        max_tokens=max_tokens
    )

    print(
        f"Backend Response Length: {len(text)}"
    )

    print(
        f"Backend Time: {time.time() - start:.2f}s"
    )

    with open(
        "backend_response.txt",
        "w",
        encoding="utf-8"
    ) as f:
        f.write(text)

    clean_text = text

    clean_text = clean_text.replace(
        "```json",
        ""
    )

    clean_text = clean_text.replace(
        "```",
        ""
    )

    clean_text = clean_text.strip()

    try:

        data = json.loads(
            clean_text
        )

    except json.JSONDecodeError as e:

        print(
            "\n=== BACKEND JSON ERROR ==="
        )

        print(e)

        print(
            "\nBackend Response Preview:"
        )

        print(
            clean_text[:500]
        )

        with open(
            "backend_failed_response.txt",
            "w",
            encoding="utf-8"
        ) as f:
            f.write(clean_text)

        raise HTTPException(
            status_code=500,
            detail="Backend returned invalid JSON."
        )

    if not isinstance(
        data,
        dict
    ):

        raise HTTPException(
            status_code=500,
            detail="Backend response is not a JSON object."
        )

    if "files" not in data:

        raise HTTPException(
            status_code=500,
            detail="Backend response missing 'files' field."
        )

    if not isinstance(
        data["files"],
        list
    ):

        raise HTTPException(
            status_code=500,
            detail="'files' must be a list."
        )

    for file in data["files"]:

        if not isinstance(
            file,
            dict
        ):

            raise Exception(
                "Invalid file object generated."
            )

        if "path" not in file:

            raise Exception(
                "Generated file missing path."
            )

        if "content" not in file:

            raise Exception(
                f"Generated file missing content: {file}"
            )

        path = file["path"]

        path = path.strip()

        path = path.replace(
            "__init__.igma py",
            "__init__.py"
        )

        path = path.replace(
            "__init__.igma",
            "__init__.py"
        )

        file["path"] = path

        content = file["content"]

        if any(
            ord(c) > 127
            for c in path
        ):

            raise Exception(
                f"Invalid file path generated: {path}"
            )

        if any(
            ord(c) > 127
            for c in content
        ):

            raise Exception(
                f"Non ASCII characters found in {path}"
            )

        if not (
            path.endswith(".py")
            or path.endswith(".txt")
        ):

            with open(
                "backend_invalid_paths.txt",
                "a",
                encoding="utf-8"
            ) as f:

                f.write(
                    f"{path}\n"
                )

            raise Exception(
                f"Invalid file extension: {path}"
            )

    validated = BackendPlan(
        **data
    )

    print(
        "=== END BACKEND ==="
    )

    return validated.model_dump()

except HTTPException:

    raise

except Exception as e:

    print(
        "BACKEND ERROR:",
        e
    )

    raise HTTPException(
        status_code=500,
        detail=(
            f"ForgeAI backend generation failed: {str(e)}"
        )
    )
````

```
```
