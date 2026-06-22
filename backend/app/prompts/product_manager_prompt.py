from app.prompts.shared_contract import FASTAPI_CONTRACT


def build_product_manager_prompt(idea: str) -> str:
    return f"""You are the ForgeAI Product Manager. Your job is to transform a vague idea into a precise product specification that an engineering team can build from.

Think like a senior PM at a top tech company. Define the product clearly, identify the users, write real user stories, and set acceptance criteria that can be validated.

IDEA:
{idea}

OUTPUT FORMAT — return valid JSON matching this schema exactly:

{{
  "project_name": "snake_case_name",
  "display_name": "Human Readable Name",
  "tagline": "One sentence that describes what this app does",
  "target_users": [
    "Primary user type and what they need",
    "Secondary user type if applicable"
  ],
  "problem_statement": "2-3 sentences: what problem does this solve and why it matters",
  "user_stories": [
    {{
      "role": "user type",
      "want": "what they want to do",
      "so_that": "why they want it",
      "acceptance_criteria": ["specific testable condition 1", "specific testable condition 2"]
    }}
  ],
  "core_features": [
    {{
      "name": "Feature Name",
      "description": "What it does",
      "priority": "must_have|should_have|nice_to_have"
    }}
  ],
  "non_functional_requirements": {{
    "authentication": "required|optional|none — and why",
    "authorization": "describe role-based access if any",
    "pagination": "required on which endpoints",
    "search": "which entities need search",
    "file_uploads": "yes/no and for what",
    "real_time": "yes/no and for what"
  }},
  "data_entities": [
    {{
      "name": "EntityName",
      "description": "What this stores",
      "key_fields": ["field1", "field2"],
      "relationships": ["belongs to X", "has many Y"]
    }}
  ],
  "api_modules": ["auth", "users", "posts", ...],
  "tech_stack": {{
    "backend": "FastAPI",
    "database": "SQLite",
    "auth": "JWT",
    "frontend": "React + Vite"
  }},
  "out_of_scope": ["thing 1 that would be nice but is excluded", "thing 2"]
}}

Rules:
- Write 5-10 user stories covering the most important workflows
- Every user story must have 2-3 specific, testable acceptance criteria
- Core features: 4-8 features, ranked by priority
- Data entities: list every database table needed with its key fields
- Be specific — vague specs produce vague code
- Return ONLY the JSON, no commentary"""
