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

"roadmap": [
{{
"phase": "",
"milestones": []
}}
]
}}

STRICT RULES:

database_entities MUST be objects.

Example:

[
{{
"name": "User",
"fields": [
"id",
"email",
"password_hash"
]
}}
]

api_modules MUST be objects.

Example:

[
{{
"name": "Authentication",
"endpoints": [
"/login",
"/register",
"/logout"
]
}}
]

roadmap MUST be an array of objects.

Example:

[
{{
"phase": "Phase 1",
"milestones": [
"Setup project",
"Create database"
]
}}
]

LIMITS:

* Maximum 5 core_features
* Maximum 3 future_features
* Maximum 3 database_entities
* Maximum 3 api_modules
* Maximum 3 roadmap phases

JSON RULES:

* Return valid JSON only
* No markdown
* No code fences
* No explanations
* No headings
* No comments

The response MUST start with {{
and end with }}
"""