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

Rules:

- Return JSON only
- No markdown
- No explanations
- No code fences
- Maximum 8 files
- Generate runnable code
- Imports must match generated paths
- Keep files concise
"""