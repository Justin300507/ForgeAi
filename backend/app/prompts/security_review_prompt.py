def build_security_review_prompt(project_path_files: dict) -> str:
    """
    project_path_files: {relative_path: file_content} for all .py files
    """
    files_block = ""
    for path, content in list(project_path_files.items())[:30]:
        files_block += f"\n\n=== {path} ===\n{content[:3000]}"

    return f"""You are the ForgeAI Security Reviewer. Audit this FastAPI codebase for security vulnerabilities before it ships.

Check for:
1. AUTHENTICATION GAPS — endpoints that should require auth but don't have `Depends(oauth2_scheme)`
2. HARDCODED SECRETS — SECRET_KEY, passwords, API keys hardcoded in source (not from env)
3. SQL INJECTION — raw string SQL queries (f-strings in db.execute())
4. MASS ASSIGNMENT — Pydantic schemas that expose fields that should be server-set (id, created_at, hashed_password)
5. INSECURE DIRECT OBJECT REFERENCE — GET /items/{{id}} without checking item.user_id == current_user.id
6. MISSING INPUT VALIDATION — string fields without length limits (use max_length=)
7. CORS TOO PERMISSIVE — allow_origins=["*"] in production code
8. PASSWORD EXPOSURE — any endpoint returning hashed_password in response
9. JWT ISSUES — weak SECRET_KEY ("secret", "password", "key"), no expiry on tokens
10. RATE LIMITING ABSENT — login/register endpoints with no rate limit mention

CODEBASE:
{files_block}

OUTPUT FORMAT — return valid JSON:

{{
  "security_score": 85,
  "risk_level": "low|medium|high|critical",
  "findings": [
    {{
      "severity": "critical|high|medium|low|info",
      "category": "AuthenticationGap|HardcodedSecret|SQLInjection|MassAssignment|IDOR|MissingValidation|WeakJWT|PasswordExposure|CORSIssue",
      "file": "app/routes/task_routes.py",
      "line_hint": "approximate line or function name",
      "description": "specific problem found",
      "recommendation": "exact fix to apply"
    }}
  ],
  "critical_count": 0,
  "high_count": 1,
  "passed_checks": ["No SQL injection found", "Passwords properly hashed with bcrypt"],
  "summary": "2-3 sentence security assessment"
}}

Return ONLY the JSON."""
