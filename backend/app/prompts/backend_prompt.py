def build_backend_prompt(architecture):
    return f"""
You are a senior FastAPI backend engineer.

Given this architecture:

{architecture}

Return ONLY valid JSON.

Generate ONLY 2 backend files.

Format:

{{
  "files": [
    {{
      "path": "app/main.py",
      "content": "# FastAPI placeholder"
    }},
    {{
      "path": "app/routes.py",
      "content": "# Routes placeholder"
    }}
  ]
}}

Rules:
- Return JSON only
- No markdown
- No explanations
- No code fences
- Maximum 2 files
- Keep content under 20 lines per file
"""