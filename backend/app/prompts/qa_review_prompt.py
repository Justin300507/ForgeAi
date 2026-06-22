def build_qa_review_prompt(product_spec: dict, architecture: dict, files: dict) -> str:
    import json
    stories = product_spec.get("user_stories", [])
    stories_str = "\n".join(
        f"- As a {s['role']}, I want {s['want']}: {s.get('acceptance_criteria', [])}"
        for s in stories[:8]
    )
    endpoints = architecture.get("api_endpoints", [])
    endpoints_str = "\n".join(
        f"  {e.get('method')} {e.get('path')}"
        for e in endpoints[:20]
    )
    # Sample key files for review
    files_block = ""
    priority_files = ["app/main.py", "app/database.py"]
    route_files = [p for p in files if "routes" in p][:4]
    for path in priority_files + route_files:
        if path in files:
            files_block += f"\n\n=== {path} ===\n{files[path][:2000]}"

    return f"""You are the ForgeAI QA Engineer. Your job is to review the generated application against the product spec and verify it's actually correct.

USER STORIES TO VALIDATE:
{stories_str}

IMPLEMENTED ENDPOINTS:
{endpoints_str}

GENERATED CODE (sample):
{files_block}

QA CHECKLIST:
1. COVERAGE — Does every user story have at least one endpoint that supports it?
2. MISSING CRUD — If user stories mention creating/reading/updating/deleting X, are all 4 verbs present?
3. AUTH COVERAGE — If spec says authentication is required, does every protected endpoint have Depends()?
4. RESPONSE MODELS — Do endpoints have response_model set? Does the schema match what's returned?
5. ERROR CASES — Are 404, 401, 403, 422 errors properly raised for edge cases?
6. HEALTH ENDPOINT — Is GET /health implemented?
7. SCHEMA COMPLETENESS — Do Pydantic schemas have all the fields the frontend would need?
8. RELATIONSHIP HANDLING — If spec has "has many" relationships, are nested endpoints present?

OUTPUT FORMAT — return valid JSON:

{{
  "qa_score": 80,
  "user_story_coverage": [
    {{
      "story": "As a user I want to create a task",
      "covered": true,
      "endpoint": "POST /tasks",
      "gap": null
    }},
    {{
      "story": "As a user I want to filter tasks by status",
      "covered": false,
      "endpoint": null,
      "gap": "GET /tasks has no status query parameter"
    }}
  ],
  "missing_features": [
    "GET /health endpoint not found",
    "No pagination on GET /posts — returns all rows"
  ],
  "code_quality_issues": [
    {{
      "file": "app/routes/task_routes.py",
      "issue": "No 404 raised when task not found in GET /tasks/{{id}}",
      "fix": "Add: if not task: raise HTTPException(404, 'Task not found')"
    }}
  ],
  "passed": ["All user CRUD endpoints present", "Auth endpoints implemented"],
  "ready_to_ship": false,
  "blockers": ["Missing /health endpoint", "No pagination on list endpoints"]
}}

Return ONLY the JSON."""
