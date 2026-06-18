def build_planner_prompt(idea):
    return f"""
You are ForgeAI's Planner Agent.

Given this idea:

{idea}

Return ONLY valid JSON.

Return JSON in EXACTLY this format:

{{
    "project_name": "",
    "description": "",

    "target_users": [],

    "core_features": [],

    "future_features": [],

    "tech_stack": [],

    "database_entities": [
        {{
            "name": "",
            "fields": []
        }}
    ],

    "api_modules": [
        {{
            "name": "",
            "endpoints": []
        }}
    ],

    "pages": [],

    "backend_modules": [],

    "roadmap": [
        {{
            "phase": "",
            "milestones": []
        }}
    ]
}}

STRICT RULES

database_entities MUST be objects.

api_modules MUST be objects.

pages MUST be an array of strings.

backend_modules MUST be an array of strings.

roadmap MUST be an array of objects.

LIMITS

- Maximum 5 core_features
- Maximum 3 future_features
- Maximum 3 database_entities
- Maximum 3 api_modules
- Maximum 5 pages
- Maximum 5 backend_modules
- Maximum 3 roadmap phases

REQUIRED FIELDS

ALL fields shown in the schema are REQUIRED.

Do not omit any field.

If a value is unknown:

- Use an empty string ""
- Use an empty array []

Never remove fields.

JSON RULES

- Return valid JSON only
- No markdown
- No code fences
- No explanations
- No headings
- No comments
- No notes
- No extra text before JSON
- No extra text after JSON

FORBIDDEN RESPONSES

Do NOT return:

{{
    "error": "..."
}}

Do NOT return:

{{
    "message": "..."
}}

Do NOT return:

{{
    "status": "..."
}}

Do NOT return any schema other than the one specified above.

VALIDATION CHECK

Before returning:

1. Ensure project_name exists.
2. Ensure description exists.
3. Ensure target_users exists.
4. Ensure core_features exists.
5. Ensure future_features exists.
6. Ensure tech_stack exists.
7. Ensure database_entities exists.
8. Ensure api_modules exists.
9. Ensure pages exists.
10. Ensure backend_modules exists.
11. Ensure roadmap exists.

The response MUST start with {{
and end with }}

Return JSON only.
"""