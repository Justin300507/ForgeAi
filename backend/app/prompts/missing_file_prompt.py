ddef build_missing_file_prompt(
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

========================================
PROJECT RULES
========================================

- Use FastAPI for backend
- Use React for frontend
- Use APIRouter for route files
- Use imports beginning with app
- Generate runnable code
- Keep implementation minimal
- Preserve architecture consistency

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

- Pydantic models
- Valid fields
- Valid Python syntax

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