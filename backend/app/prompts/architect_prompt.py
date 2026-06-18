import json


def build_architect_prompt(project_plan):

    return f"""
You are a senior software architect.

Project Plan JSON:

{json.dumps(project_plan, indent=2)}

IMPORTANT:

Do NOT generate:

- plan
- backend
- frontend
- validation
- runtime
- stats
- project_path
- zip_path
- metadata_path

Generate ONLY architecture data.

Generate ONLY valid JSON.

The response MUST start with {{ and end with }}.

Your response MUST contain ONLY these top-level keys:

- api_endpoints
- database_schema
- folder_structure
- frontend_structure

Any other key is invalid.

Do not leave arrays unfinished.
Do not leave objects unfinished.
Do not truncate JSON.

Format:

{{
  "api_endpoints": [
    {{
      "method": "",
      "path": "",
      "description": ""
    }}
  ],

  "database_schema": [
    {{
      "table_name": "",
      "columns": [
        {{
          "name": "",
          "type": "",
          "is_primary_key": false,
          "is_nullable": true
        }}
      ]
    }}
  ],

  "folder_structure": {{
    "backend": []
  }},

  "frontend_structure": {{
    "pages": [],
    "components": []
  }}
}}

Rules:

- Return valid JSON only
- No markdown
- No code blocks
- No explanations
- No text before JSON
- No text after JSON

IMPORTANT LIMITS:

- Maximum 5 API endpoints
- Maximum 3 database tables
- Maximum 5 backend folders
- Maximum 5 frontend pages
- Maximum 5 frontend components

API Rules:

- Generate realistic REST API endpoints
- Include CRUD operations where appropriate
- Use industry-standard endpoint naming

Database Rules:

- Every table must have a primary key
- Primary keys must not be nullable
- Include realistic column types
- Add foreign key columns when relationships exist

Frontend Structure Rules:

- Generate pages based on project requirements
- Generate reusable components
- Use realistic component names

Folder Structure Rules:

- Generate production-ready backend folders

Return JSON only.
"""