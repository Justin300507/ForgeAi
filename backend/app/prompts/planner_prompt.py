def build_planner_prompt(idea):
    return f"""
You are a senior software product manager.

Return ONLY valid JSON.

Idea:
{idea}

Format:

{{
  "project_name": "",
  "description": "",
  "target_users": [],
  "core_features": [],
  "future_features": [],
  "tech_stack": [],
  "database_entities": [],
  "api_modules": [],
  "roadmap": []
}}
IMPORTANT:
roadmap MUST be an array of objects.
Each object must contain:
- phase (string)
- milestones (array of strings)

Do NOT return roadmap as plain text.
Do not return markdown.
Do not use code blocks.
Return valid JSON only.
IMPORTANT:

Return ONLY valid JSON.

Do NOT return markdown.
Do NOT return explanations.
Do NOT return headings.
Do NOT return code fences.

The response MUST start with:

 and end with curly bracket and end with curly bracket
IMPORTANT:
Maximum:
- 5 core_features
- 3 future_features
- 3 database_entities
- 3 api_modules
- 3 roadmap phases
"""