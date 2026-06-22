def build_performance_review_prompt(files: dict, architecture: dict) -> str:
    file_blocks = "\n\n".join(
        f"=== {path} ===\n{content[:2000]}"
        for path, content in list(files.items())[:20]
    )
    db_schema = architecture.get("db_schema", {})
    db_info = str(db_schema)[:500] if db_schema else "Not specified"

    return f"""You are a performance engineer reviewing AI-generated FastAPI backend code.
Detect performance anti-patterns in this project.

DATABASE SCHEMA:
{db_info}

PROJECT FILES:
{file_blocks}

Check for these specific issues:

1. N+1 QUERY PROBLEMS:
   - Loops that execute a DB query per iteration
   - Missing joinedload/selectinload for relationships
   - Example: for user in users: db.query(Post).filter(Post.user_id == user.id).all()

2. MISSING DATABASE INDEXES:
   - Foreign key columns without Index() or index=True
   - Columns used in WHERE clauses that lack indexes
   - Unique constraints missing index=True

3. LARGE PAYLOAD RISKS:
   - Endpoints returning entire tables without pagination (limit/offset)
   - SELECT * patterns with no column restriction
   - No max_size limit on list endpoints

4. SLOW API WARNINGS:
   - Synchronous operations (time.sleep, requests.get) inside async endpoints
   - Heavy computation (loops over thousands of items) in request handlers
   - Missing database connection pooling configuration
   - Missing response caching for expensive read endpoints

Return JSON ONLY (no markdown):
{{
  "performance_score": <0-100>,
  "n_plus_one_issues": [
    {{
      "file": "app/routes/post_routes.py",
      "line_hint": "lines 45-52",
      "description": "N+1: fetches user for each post in loop",
      "fix": "Use joinedload(Post.user) in initial query"
    }}
  ],
  "missing_index_issues": [
    {{
      "model_file": "app/models/post.py",
      "column": "user_id",
      "description": "FK column user_id has no index",
      "fix": "Add index=True to Column definition"
    }}
  ],
  "large_payload_issues": [
    {{
      "endpoint": "GET /posts",
      "file": "app/routes/post_routes.py",
      "description": "Returns all posts with no pagination",
      "fix": "Add skip: int = 0, limit: int = 20 parameters"
    }}
  ],
  "slow_api_issues": [
    {{
      "file": "app/routes/report_routes.py",
      "description": "Heavy aggregation computed synchronously in handler",
      "fix": "Move to background task or cache result"
    }}
  ],
  "n_plus_one_count": <int>,
  "missing_indexes": <int>,
  "large_payload_risks": <int>,
  "slow_api_warnings": <int>,
  "passed_checks": ["list of checks that passed"],
  "summary": "one paragraph performance assessment"
}}"""
