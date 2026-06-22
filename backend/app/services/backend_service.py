import json
import time

from fastapi import HTTPException
from app.utils.json_cleaner import extract_json

from app.prompts.backend_prompt import build_backend_prompt
from app.models.backend_models import BackendPlan
from app.providers.ai_provider import generate_content


def generate_backend(
    architecture,
    provider="auto",
    max_tokens=16000
):
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

        if not text:
            raise HTTPException(
                status_code=500,
                detail="Backend LLM returned empty response (token limit likely exceeded)."
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
        clean_text = clean_text.replace("```json", "")
        clean_text = clean_text.replace("```", "")
        clean_text = clean_text.strip()

        try:
            data = extract_json(clean_text)

        except json.JSONDecodeError as e:

            print("\n=== BACKEND JSON ERROR ===")
            print(e)

            print("\nBackend Response Preview:")
            print(clean_text[:500])

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

        if not isinstance(data, dict):
            raise HTTPException(
                status_code=500,
                detail="Backend response is not a JSON object."
            )

        if "files" not in data:
            raise HTTPException(
                status_code=500,
                detail="Backend response missing 'files' field."
            )

        if not isinstance(data["files"], list):
            raise HTTPException(
                status_code=500,
                detail="'files' must be a list."
            )

        good_files = []

        for file in data["files"]:

            if not isinstance(file, dict):
                print(f"Skipping invalid file entry (not a dict): {file}")
                continue

            if "path" not in file or "content" not in file:
                print(f"Skipping file entry missing path/content: {file}")
                continue

            path = file["path"].strip()

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

            if (
                path.endswith(".py")
                and "__init__.py" not in path
                and not content.strip()
            ):
                print(
                    f"Skipping empty generated file: {path} "
                    f"(validation/Missing File Agent will fill this in)"
                )
                continue

            if any(ord(c) > 127 for c in path):
                print(f"Skipping file with invalid (non-ASCII) path: {path}")
                continue

            if not (path.endswith(".py") or path.endswith(".txt")):

                with open(
                    "backend_invalid_paths.txt",
                    "a",
                    encoding="utf-8"
                ) as f:
                    f.write(f"{path}\n")

                print(f"Skipping file with invalid extension: {path}")
                continue

            good_files.append(file)

        if not good_files:
            raise HTTPException(
                status_code=500,
                detail="Backend generated no valid files."
            )

        data["files"] = good_files

        validated = BackendPlan(**data)

        print(
            "=== END BACKEND ==="
        )

        return validated.model_dump()

    except HTTPException:
        raise

    except Exception as e:
        print("BACKEND ERROR:", e)
        raise HTTPException(
            status_code=500,
            detail=(
                f"ForgeAI backend generation failed: {str(e)}"
            )
        )