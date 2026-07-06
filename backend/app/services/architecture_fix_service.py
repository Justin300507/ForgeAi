import json
import re

from app.providers.ai_provider import generate_content
from app.utils.json_cleaner import extract_json
from app.prompts.shared_contract import FASTAPI_CONTRACT


def generate_architecture_fix(
    architecture,
    validation_errors,
    provider,
    required_exports=None,
    required_endpoints=None,
    existing_symbols=None
):

    required_exports = required_exports or {}
    required_endpoints = required_endpoints or {}
    existing_symbols = existing_symbols or {}

    exports_block = ""
    if required_exports:
        exports_block = "REQUIRED EXPORTS — DO NOT REMOVE OR RENAME THESE:\n"
        for file_path, symbols in required_exports.items():
            exports_block += f"{file_path} MUST still export: {', '.join(sorted(symbols))}\n"

    endpoints_block = ""
    if required_endpoints:
        endpoints_block = "REQUIRED ENDPOINTS — ALL OF THESE MUST EXIST IN YOUR OUTPUT:\n"
        for file_path, endpoints in required_endpoints.items():
            endpoints_block += f"{file_path} MUST implement: {', '.join(endpoints)}\n"

    existing_block = ""
    if existing_symbols:
        existing_block = (
            "EXISTING SYMBOLS — REUSE THESE EXACT NAMES, "
            "DO NOT INVENT NEW ONES (e.g. do not write TaskOut if "
            "TaskResponse already exists below):\n"
        )
        for file_path, names in existing_symbols.items():
            existing_block += f"{file_path} already defines: {', '.join(names)}\n"

    prompt = f"""
You are ForgeAI Architecture Repair Agent.

{FASTAPI_CONTRACT}

{exports_block}

{endpoints_block}

{existing_block}

Architecture:

{json.dumps(architecture, indent=2)}

Validation Errors:

{json.dumps(validation_errors, indent=2)}

Your task:

Regenerate ONLY the files necessary to fix the validation errors.

Requirements:

- Fix every validation error
- Preserve architecture
- Preserve endpoints
- Preserve models
- Preserve routes
- Preserve services
- Preserve every symbol listed under REQUIRED EXPORTS above, exactly as named
- Implement every endpoint listed under REQUIRED ENDPOINTS above, exactly as listed,
  including the exact path string with no missing prefixes
- Reuse every symbol listed under EXISTING SYMBOLS above — do not invent
  alternative names for things that already exist elsewhere in the project
- NEVER reference a service, model, or schema class unless you also
  generate that exact file in this same response
- Follow the PROJECT CONTRACT above exactly

Return ONLY valid JSON.

Format:

{{
    "files": [
        {{
            "path": "",
            "content": ""
        }}
    ]
}}

========================================
CRITICAL OUTPUT RULES
========================================

You MUST return JSON only.

Do NOT explain.

Do NOT describe.

Do NOT provide tutorials.

Do NOT provide markdown.

Do NOT provide python code blocks.

Do NOT provide examples.

Do NOT provide headings.

Do NOT create app.py.

Do NOT regenerate the entire project.

Only return files that need changes.

The first character of your response MUST be {{

The last character of your response MUST be }}

Return JSON only.
"""

    text = generate_content(prompt, provider, max_tokens=8000, thinking_budget=512, stage="architecture_fix")
    print("\n=== ARCHITECTURE FIX RAW RESPONSE ===")
    print(text[:3000])

    try:

        text = text.strip()

        if text.startswith("```json"):
            text = text[7:]

        if text.startswith("```"):
            text = text[3:]

        if text.endswith("```"):
            text = text[:-3]

        text = text.strip()

        match = re.search(r"\{.*\}", text, re.DOTALL)

        if match:
            text = match.group(0)

        return extract_json(text)

    except Exception as e:

        print("\n=== ARCHITECTURE FIX JSON ERROR ===")
        print(str(e))
        print("\nResponse Preview:")
        print(text[:1000])

        return None