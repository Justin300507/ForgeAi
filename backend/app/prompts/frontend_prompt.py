def build_frontend_prompt(architecture):

    return f"""
You are a senior React frontend engineer.

Given this architecture:

{architecture}

Return ONLY valid JSON.

Generate ONLY 2 frontend files.

Format:

{{
  "files": [
    {{
      "path": "src/App.jsx",
      "content": "// React placeholder"
    }},
    {{
      "path": "src/pages/Home.jsx",
      "content": "// Home page placeholder"
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