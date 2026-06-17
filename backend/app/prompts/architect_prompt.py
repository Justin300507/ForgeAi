def build_architect_prompt(project_plan):
    return f"""
You are a senior software architect.

Given this project plan:

{project_plan}

Generate ONLY valid JSON.

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
  }}
}}

Rules:

- Return valid JSON only
- No markdown
- No code blocks
- No explanations

IMPORTANT:
Generate EXACTLY:

- 3 API endpoints
- 2 database tables
- 5 backend folders

Do not generate anything else.
API Rules:
- Generate realistic REST API endpoints
- Include CRUD operations where appropriate
- Use industry-standard endpoint naming

Database Rules:
- Every table must have a primary key
- Primary keys must not be nullable
- Include realistic column types
- Add foreign key columns when relationships exist

Folder Structure Rules:
- Generate a production-ready backend structure
- Use common backend folders
- Include:
  - routes
  - controllers
  - services
  - models
  - middleware
  - config
  - utils
  - validators

Think like a senior software architect designing a scalable SaaS application.
IMPORTANT:
Generate a maximum of:
- 5 API endpoints
- 3 database tables
- 8 backend folders
"""