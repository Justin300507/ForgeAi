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

Do not return markdown.
Do not use code blocks.
Return valid JSON only.
"""