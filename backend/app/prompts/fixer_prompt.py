from app.prompts.shared_contract import FASTAPI_CONTRACT


def build_fixer_prompt(
    file_path,
    file_content,
    errors
):
    error_text = "\n".join(
        f"- {e}"
        for e in errors
    )

    return f"""

You are ForgeAI Fix Agent.

File Path:

{file_path}

Current File Content:

{file_content}

Validation Errors:

{error_text}

Your task:

Generate a COMPLETE corrected version of this file.

REPAIR RULES

* Fix ALL validation errors together.
* Return the ENTIRE corrected file.
* Do not return patches.
* Do not return individual functions.
* Do not omit existing working code.
* Preserve imports when possible.
* Keep existing working code unless it conflicts with a validation error.

SYMBOL REPAIR RULES

If validation error contains:

Missing symbol 'user_router'

Then the repaired file MUST export:

user_router

Valid:

user_router = APIRouter()

Invalid:

router = APIRouter()

If validation error contains:

Missing symbol 'task_router'

Then the repaired file MUST export:

task_router

Valid:

task_router = APIRouter()

Invalid:

router = APIRouter()

Always create the missing symbol exactly as requested.

ROUTE QUALITY RULES

If validation error contains:

No endpoints found

Then generate at least one valid endpoint.

Example:

@user_router.get("/")
def get_users():
return []

A route file is invalid if it only contains:

user_router = APIRouter()

without any endpoints.

If a route file is repaired:

* Ensure APIRouter exists.
* Ensure at least one endpoint exists.
* Ensure the router variable matches the imported symbol name.

DEPENDENCY REPAIR RULES

If validation error contains:

Unknown dependency:

Replace the invalid dependency with the closest valid package.

Examples:

e-mail-validator -> email-validator
eval-validator -> email-validator
echo-validator -> email-validator
pythonmultipart -> python-multipart
fast-api -> fastapi

Never invent package names.

If repairing requirements.txt:

Return the ENTIRE corrected requirements.txt content.

{FASTAPI_CONTRACT}

REACT RULES

* Use React functional components.
* Export default component.
* Generate valid JSX.
* Do not use TypeScript.

PATH RULES

Frontend files must be inside src/

Backend files must be inside app/

Requirements file path:

app/requirements.txt

OUTPUT FORMAT

Return ONLY valid JSON.

{{
"path": "{file_path}",
"content": "FULL FILE CONTENT"
}}

OUTPUT RULES

* JSON only
* No markdown
* No explanations
* No code fences
* No text before JSON
* No text after JSON
* Only escape these characters: \\" \\\\ \\n \\t \\r
* NEVER put a backslash before any other character (e.g. @, (, ))
* A decorator like @user_router.get(...) needs NO backslash before @

Return valid JSON only.
"""