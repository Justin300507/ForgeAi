import json
import re
import time

from fastapi import HTTPException

from app.prompts.frontend_prompt import build_frontend_prompt
from app.models.frontend_models import FrontendPlan
from app.providers.ai_provider import generate_content
from app.utils.json_cleaner import extract_json


def _fix_jsx_brace_errors(content: str) -> str:
    """
    Fix the most common LLM JSX brace bug: style={{...}}}> (3 closing braces
    instead of 2 before a tag-close '>').  Also fixes the symmetric case with
    a trailing space/newline after the extra brace.
    """
    # }}}> → }}> (extra closing brace before tag-close)
    content = re.sub(r'\}\}\}>', '}}>', content)
    # }}}} → }}} (double extra braces, rare but seen)
    content = re.sub(r'\}\}\}\}>', '}}}>', content)
    return content


def _fix_jsx_truncated_templates(content: str) -> str:
    """
    Fix JSX files where LLM output was cut off mid-template-literal.

    Three-pass fix:
    1. Per-line ${...}: if a line has more ${ opens than } closes, close them.
    2. Per-line backtick: if a line has an odd number of backticks, the template
       literal was opened but never closed on that line — append a closing `.
       This handles plain-string truncations like `transition-colo (no ${ at all).
    3. File-level: if the total backtick count is still odd, append one at the end.
    """
    lines = content.split('\n')
    result_lines = []
    for line in lines:
        # Pass 1 — close unclosed ${...} expressions
        if '${' in line:
            opens = line.count('${')
            first_pos = line.find('${')
            after_first = line[first_pos:]
            closes = after_first.count('}')
            unmatched = opens - closes
            if unmatched > 0:
                last_pos = line.rfind('${')
                after_last = line[last_pos:]
                suffix = ''
                if after_last.count("'") % 2 == 1:
                    suffix += "'"
                if after_last.count('"') % 2 == 1:
                    suffix += '"'
                suffix += '}' * unmatched
                line = line.rstrip() + suffix

        # Pass 2 — close unclosed backtick template literals on this line
        # An odd backtick count means one template literal was opened but not closed.
        if line.count('`') % 2 == 1:
            line = line.rstrip() + '`'

        result_lines.append(line)

    content = '\n'.join(result_lines)

    # Pass 3 — file-level safety net
    if content.count('`') % 2 == 1:
        content = content.rstrip() + '`'

    return content


def generate_frontend(
    architecture,
    provider="auto",
    max_tokens=24000,
    frontend_target: str = "web",
    idea: str = "",
):
    try:

        print("\n=== START FRONTEND ===")

        prompt = build_frontend_prompt(architecture, target=frontend_target, idea=idea)

        from app.utils.llm_cache import get_cached, set_cached
        _cache_payload = {"prompt": prompt, "mt": max_tokens}
        _cached_data = get_cached("frontend", _cache_payload)
        if _cached_data is not None:
            print("Frontend: [cache hit] — skipping LLM call")
            print("=== END FRONTEND ===")
            return _cached_data

        data = None
        for attempt in range(3):
            start = time.time()
            text = generate_content(prompt, provider, max_tokens=max_tokens)
            print(f"Frontend Time: {time.time() - start:.2f}s")

            if not text:
                print(f"Frontend attempt {attempt+1}: empty response, retrying...")
                continue

            with open("frontend_response.txt", "w", encoding="utf-8") as f:
                f.write(text)

            clean_text = text.replace("```json", "").replace("```", "").strip().replace("\t", " ")
            print(f"Frontend Response Length: {len(clean_text)}")

            try:
                data = extract_json(clean_text)
                break
            except json.JSONDecodeError as e:
                print(f"\n=== FRONTEND JSON ERROR (attempt {attempt+1}) ===")
                print(e)
                with open("frontend_failed_response.txt", "w", encoding="utf-8") as f:
                    f.write(clean_text)
                if attempt < 2:
                    print("Retrying frontend generation...")
                    continue

        if data is None:
            raise HTTPException(
                status_code=500,
                detail="Frontend returned invalid JSON after 3 attempts."
            )

        if not isinstance(
            data,
            dict
        ):

            raise HTTPException(
                status_code=500,
                detail="Frontend response is not a JSON object."
            )

        if "files" not in data:

            raise HTTPException(
                status_code=500,
                detail="Frontend response missing 'files' field."
            )

        if not isinstance(data["files"], list):

            raise HTTPException(
                status_code=500,
                detail="'files' must be a list."
            )

        good_files = []

        for file in data["files"]:

            if not isinstance(file, dict):
                print(f"Skipping invalid frontend file entry (not a dict): {file}")
                continue

            if "path" not in file or "content" not in file:
                print(f"Skipping frontend file entry missing path/content: {file}")
                continue

            path = file["path"].strip()
            file["path"] = path
            content = _fix_jsx_brace_errors(file["content"])
            content = _fix_jsx_truncated_templates(content)
            file["content"] = content

            if not content.strip():
                print(f"Skipping empty generated frontend file: {path}")
                continue

            if any(ord(c) > 127 for c in path):
                print(f"Skipping frontend file with invalid (non-ASCII) path: {path}")
                continue

            if not path.endswith((".jsx", ".js", ".css")):
                print(f"Skipping frontend file with invalid extension: {path}")
                continue

            if not path.startswith("src/"):
                print(f"Skipping frontend file with invalid root: {path}")
                continue

            good_files.append(file)

        if not good_files:

            raise HTTPException(
                status_code=500,
                detail="Frontend generated no valid files."
            )

        data["files"] = good_files

        validated = FrontendPlan(
            **data
        )

        print(
            "=== END FRONTEND ==="
        )

        result = validated.model_dump()
        set_cached("frontend", _cache_payload, result)
        return result

    except HTTPException:

        raise

    except Exception as e:

        print(
            "FRONTEND ERROR:",
            e
        )

        raise HTTPException(
            status_code=500,
            detail=(
                f"ForgeAI frontend generation failed: {str(e)}"
            )
        )