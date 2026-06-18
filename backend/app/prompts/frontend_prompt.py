def build_frontend_prompt(architecture):

    return f"""
You are a senior React frontend engineer.

Given this architecture:

{architecture}

Return ONLY valid JSON.

Generate a runnable React frontend.

Required files:

- src/App.jsx

For each page generate:

- pages/<Page>.jsx

For each component generate:

- components/<Component>.jsx

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
- Generate runnable React code
- Imports must match generated paths
- Keep files concise
"""