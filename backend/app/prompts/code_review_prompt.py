def build_code_review_prompt(files: dict, architecture: dict) -> str:
    file_blocks = "\n\n".join(
        f"=== {path} ===\n{content[:2000]}"
        for path, content in list(files.items())[:20]
    )
    endpoints = architecture.get("api_endpoints", [])
    endpoint_list = "\n".join(
        f"  {ep.get('method')} {ep.get('path')} → {ep.get('file')}"
        for ep in endpoints[:20]
    )

    return f"""You are a senior code reviewer auditing AI-generated Python backend code.
Review the following FastAPI project files for code quality.

ARCHITECTURE ENDPOINTS:
{endpoint_list}

PROJECT FILES:
{file_blocks}

Evaluate these four dimensions (0-100 each):

1. NAMING QUALITY (naming_score):
   - Are functions, classes, variables named clearly and consistently?
   - Do route files follow {{resource}}_router naming?
   - Are schemas clearly named (UserCreate, UserResponse vs UserIn, UserOut)?
   - Penalize: single-letter vars in business logic, ambiguous names, misleading names

2. ARCHITECTURE QUALITY (architecture_score):
   - Is the separation of concerns maintained (routes→services→models)?
   - Are route handlers thin (delegating to services)?
   - Is business logic kept out of route handlers?
   - Are models, schemas, and routes in their proper directories?

3. MAINTAINABILITY (maintainability_score):
   - Are functions small and focused (single responsibility)?
   - Is duplicate code avoided?
   - Are magic strings/numbers extracted to constants?
   - Is error handling consistent?

4. TECHNICAL DEBT (tech_debt_score, higher = less debt):
   - Are there TODO/FIXME/HACK/pass stubs remaining?
   - Are there hardcoded values that should be config?
   - Are there deeply nested conditionals that should be refactored?
   - Are there copy-pasted code blocks?

For each issue, include: file, line_hint, severity (critical|high|medium|low), category, description, suggestion.

Return JSON ONLY (no markdown):
{{
  "naming_score": <0-100>,
  "architecture_score": <0-100>,
  "maintainability_score": <0-100>,
  "tech_debt_score": <0-100>,
  "overall_score": <weighted average>,
  "issues": [
    {{
      "severity": "high",
      "category": "naming|architecture|maintainability|tech_debt",
      "file": "app/routes/user_routes.py",
      "line_hint": "line 42",
      "description": "Route handler contains 50 lines of business logic",
      "suggestion": "Extract to user_service.py::create_user_service()"
    }}
  ],
  "positives": ["list of things done well"],
  "summary": "one paragraph review summary"
}}"""
