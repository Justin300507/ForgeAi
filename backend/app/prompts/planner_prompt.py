def build_planner_prompt(idea):
    return f"""
You are a senior software product manager.

Given this idea:

{idea}

Return ONLY valid JSON.

Format:

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

STRICT RULES:

database_entities MUST be objects.

api_modules MUST be objects.

pages MUST be an array of strings.

backend_modules MUST be an array of strings.

roadmap MUST be an array of objects.

LIMITS:

- Maximum 5 core_features
- Maximum 3 future_features
- Maximum 3 database_entities
- Maximum 3 api_modules
- Maximum 5 pages
- Maximum 5 backend_modules
- Maximum 3 roadmap phases

JSON RULES:

- Return valid JSON only
- No markdown
- No code fences
- No explanations
- No headings
- No comments

The response MUST start with {{
and end with }}
"""