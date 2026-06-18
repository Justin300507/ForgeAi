def build_fixer_prompt(error):
    return f"""
```

You are a senior software engineer.

Fix this validation error:

{error}

Return ONLY valid JSON.

Format:

{{
"path": "",
"content": ""
}}

STRICT RULES:

* Generate ONLY the missing file
* Generate a minimal working implementation
* Keep file under 30 lines
* Return valid JSON only
* No markdown
* No explanations
* No code fences
* Escape all quotes correctly
* Escape all backslashes correctly
* The response must start with {{
* The response must end with }}

PATH RULES:

Frontend files MUST be placed inside src/

Examples:

Missing frontend import target: ./pages/Login.jsx
→ src/pages/Login.jsx

Missing frontend import target: ./pages/TaskDetail.jsx
→ src/pages/TaskDetail.jsx

Missing frontend import target: ./components/UserAvatar.jsx
→ src/components/UserAvatar.jsx

Missing frontend import target: ./PriorityBadge.jsx
→ src/components/PriorityBadge.jsx

Backend files MUST be placed inside app/

Examples:

Missing backend import target: services/auth_service.py
→ app/services/auth_service.py

Missing backend import target: services/chat_service.py
→ app/services/chat_service.py

Missing backend import target: models/user.py
→ app/models/user.py

Missing backend import target: routes/auth_routes.py
→ app/routes/auth_routes.py

IMPORTANT:

Return the FULL CORRECT project path in the path field.

Example:

{{
"path": "src/pages/TaskDetail.jsx",
"content": "..."
}}

Generate the missing file now.
"""
