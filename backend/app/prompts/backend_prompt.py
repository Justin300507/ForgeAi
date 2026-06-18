def build_backend_prompt(architecture):
    return f"""
You are a senior FastAPI backend engineer.

Given this architecture:

{architecture}

Return ONLY valid JSON.

Generate a runnable FastAPI backend.

Required files:

- app/main.py
- app/requirements.txt

For each backend module generate:

- routes/<module>_routes.py
- models/<module>.py
- services/<module>_service.py

Format:

{{
  "files": [
    {{
      "path": "",
      "content": ""
    }}
  ]
}}

CRITICAL RULES:

- Return JSON only
- No markdown
- No explanations
- No code fences

- Maximum 8 files

- Generate runnable FastAPI code

- Every imported file MUST exist
- Do NOT import files that are not generated
- All imports must match generated paths

- Use only generated models
- Use only generated services
- Use only generated routes

- Avoid placeholder imports

- main.py must successfully import all route files

- Keep code concise
- Keep files under 50 lines where possible

EXAMPLE:

If you generate:

app/routes/user_routes.py

Then imports like:

from routes.user_routes import user_router

are allowed.

Do NOT generate:

from routes.auth_routes import auth_router

unless auth_routes.py is also generated.

Generate production-style but minimal runnable code.
"""