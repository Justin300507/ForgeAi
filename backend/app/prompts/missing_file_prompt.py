def build_missing_file_prompt(
    filepath,
    error
):
    return f"""
You are ForgeAI Missing File Agent.

A required project file is missing.

Missing File:

{filepath}

Validation Error:

{error}

Your task:

Generate the COMPLETE missing file.

PROJECT RULES

- Use FastAPI for backend.
- Use React for frontend.
- Use APIRouter for routes.
- Use imports beginning with app.
- Generate runnable code.
- Keep implementation minimal.
- Preserve project architecture.

BACKEND RULES

Examples:

app/services/auth_service.py

app/routes/user_routes.py

app/models/user.py

FRONTEND RULES

Examples:

src/pages/Login.jsx

src/components/Navbar.jsx

OUTPUT FORMAT

Return ONLY valid JSON.

{{
    "path": "{filepath}",
    "content": "FULL FILE CONTENT"
}}

OUTPUT RULES

- JSON only
- No markdown
- No explanations
- No code fences
- No text before JSON
- No text after JSON

Return valid JSON only.
"""