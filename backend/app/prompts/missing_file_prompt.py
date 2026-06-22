from app.prompts.shared_contract import FASTAPI_CONTRACT


def build_missing_file_prompt(
    filepath,
    error
):
    return f"""
You are ForgeAI Missing File Agent.

A required project file is missing.

========================================
MISSING FILE
========================================

{filepath}

========================================
VALIDATION ERROR
========================================

{error}

========================================
YOUR TASK
========================================

Generate the COMPLETE missing file.

The generated file must be runnable.

The generated file must match the file path.

The generated file must follow ForgeAI architecture.

Return a COMPLETE file.

Never return partial code.

Never return placeholders.

{FASTAPI_CONTRACT}

========================================
ROUTE FILE RULES
========================================

If filepath contains:

app/routes/

Generate:

- APIRouter
- Valid route handlers
- Export router object

Example:

user_router = APIRouter()

========================================
MODEL FILE RULES
========================================

If filepath contains:

app/models/

Generate:

- SQLAlchemy models that inherit from Base (NOT Pydantic BaseModel)
- Import Base from app.database: from app.database import Base
- Valid SQLAlchemy column definitions
- Valid Python syntax

NEVER put Pydantic BaseModel subclasses in app/models/ — those belong in app/schemas/.

========================================
SERVICE FILE RULES
========================================

If filepath contains:

app/services/

Generate:

- Minimal service functions
- Valid imports
- Valid Python syntax

========================================
FRONTEND PAGE RULES
========================================

If filepath contains:

src/pages/

Generate:

- React component
- Default export
- Valid JSX

========================================
FRONTEND COMPONENT RULES
========================================

If filepath contains:

src/components/

Generate:

- Reusable React component
- Default export
- Valid JSX

========================================
CONSISTENCY RULES
========================================

- Every import must exist
- Every exported symbol must exist
- No circular imports
- No broken imports
- No undefined variables

========================================
PATH RULES
========================================

The returned path MUST exactly equal:

{filepath}

========================================
OUTPUT FORMAT
========================================

Return ONLY valid JSON.

{{
    "path": "{filepath}",
    "content": "FULL FILE CONTENT"
}}

========================================
OUTPUT RULES
========================================

- JSON only
- No markdown
- No explanations
- No code fences
- No text before JSON
- No text after JSON
- Only escape these characters: \\" \\\\ \\n \\t \\r
- NEVER put a backslash before any other character

========================================
FINAL VALIDATION
========================================

Before returning:

1. Ensure path equals the supplied filepath.
2. Ensure content is a complete file.
3. Ensure syntax is valid.
4. Ensure imports are valid.
5. Ensure JSON is valid.

Return JSON only.
"""