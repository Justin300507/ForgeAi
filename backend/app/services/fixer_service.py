from app.prompts.fixer_prompt import build_fixer_prompt
from app.providers.ai_provider import generate_content
from app.utils.json_cleaner import extract_json
from app.utils.llm_cache import get_cached, set_cached
import json


def generate_fix(
    file_path,
    file_content,
    errors,
    provider="auto",
    bypass_cache: bool = False,
    thinking_budget: int = 512,
):

    if len(file_content) > 15000:
        file_content = file_content[:15000]

    prompt = build_fixer_prompt(
        file_path,
        file_content,
        errors
    )

    _cache_payload = {"prompt": prompt}
    if not bypass_cache:
        _cached = get_cached("fix", _cache_payload)
        if _cached is not None:
            print(f"      [fix cache hit] {file_path}")
            return _cached

    text = generate_content(
        prompt,
        provider,
        max_tokens=12000,
        thinking_budget=thinking_budget,
    )

    try:
        result = extract_json(text)
        if result and result.get("content"):
            set_cached("fix", _cache_payload, result)
        return result

    except (json.JSONDecodeError, ValueError) as e:

        print("\n=== FIX AGENT JSON ERROR ===")
        print(e)

        print("\n=== RAW RESPONSE ===")
        print(text)

        print("=====================\n")

        return {
            "path": "",
            "content": ""
        }