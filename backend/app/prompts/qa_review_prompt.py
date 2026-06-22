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
5. ERROR CASES — Are 404, 401, 403 errors properly raised for edge cases? (422 is automatic from Pydantic — do NOT flag it)
6. HEALTH ENDPOINT — Is GET /health implemented?
7. SCHEMA COMPLETENESS — Do Pydantic schemas have required string fields with Field(min_length=1)?
8. PAGINATION — Do GET list endpoints have limit/offset params?
9. RELATIONSHIP HANDLING — If spec has "has many" relationships, are nested endpoints present?

SCORING RULES:
- Start at 100. Deduct points per blocker.
- DO NOT flag as blockers: external service integrations (email, SMS, push notifications, Stripe, S3) — these require secrets not available at generation time and are not testable.
- DO NOT flag as blockers: 422 responses — Pydantic validates input automatically and 422 is correct FastAPI behavior.
- Deduct 15 pts each: missing /health, no pagination on list endpoints, no auth on protected endpoints
- Deduct 10 pts each: missing CRUD verb for an explicit user story, no 404 on single-resource endpoints
- Deduct 5 pts each: missing response_model, poor schema completeness
- If all core endpoints are present and app passes static+runtime validation, minimum score is 60.

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
    "No pagination on GET /posts — returns all rows",
    "GET /items/{{id}} returns 500 instead of 404 when not found"
  ],
  "code_quality_issues": [
    {{
      "file": "app/routes/task_routes.py",
      "issue": "No 404 raised when task not found in GET /tasks/{{id}}",
      "fix": "Add: if not task: raise HTTPException(404, 'Task not found')"
    }}
  ],
  "passed": ["All user CRUD endpoints present", "Auth endpoints implemented", "GET /health present", "Pagination on list endpoints"],
  "ready_to_ship": false,
  "blockers": ["No pagination on list endpoints"]
}}

Return ONLY the JSON."""
