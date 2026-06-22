"""V12 Evolution Prompt — given live metrics, generate improvement directives."""


def build_evolution_prompt(
    idea: str,
    metrics: dict,
    architecture: dict,
    shared_contract: str = "",
) -> str:
    issues = "\n".join(f"  - {i}" for i in metrics.get("issues", [])) or "  - None detected"
    endpoint_stats = "\n".join(
        f"  {path}: avg {s['avg_ms']}ms, error_rate {s['error_rate']:.0%}"
        for path, s in metrics.get("endpoint_stats", {}).items()
    )
    endpoints_summary = "\n".join(
        f"  {ep.get('method', 'GET')} {ep.get('path', '')}"
        for ep in (architecture.get("api_endpoints") or [])[:15]
    )

    return f"""{shared_contract}

You are a performance and reliability engineer reviewing a live FastAPI application.

## Original Idea
{idea}

## Live Performance Metrics
- Availability:      {metrics.get('availability', 0):.0%}
- Avg response time: {metrics.get('avg_response_ms', 0):.0f}ms
- P95 response time: {metrics.get('p95_response_ms', 0):.0f}ms
- Error rate:        {metrics.get('error_rate', 0):.0%}
- Slowest endpoint:  {metrics.get('slowest_endpoint', 'unknown')}

## Endpoint Stats
{endpoint_stats}

## Detected Issues
{issues}

## Current API Endpoints
{endpoints_summary}

## Your Task
Analyze these metrics and produce a JSON evolution plan with specific code improvements.

Return ONLY valid JSON in this exact format:
{{
  "needs_improvement": true/false,
  "priority": "high/medium/low",
  "improvements": [
    {{
      "type": "performance|reliability|feature",
      "file": "app/routes/jobs.py",
      "description": "Add response caching to GET /jobs endpoint",
      "code_hint": "from functools import lru_cache  # cache results for 60s"
    }}
  ],
  "regeneration_context": "One paragraph describing what to do differently in the next generation to fix these issues.",
  "summary": "One-sentence summary of the main problem and fix."
}}

Focus on:
1. Database query optimization (N+1 queries, missing pagination)
2. Missing error handling (returning 500 on bad input)
3. Missing indexes on frequently-queried fields
4. Response time improvements (caching, async where applicable)

If availability >= 99% and avg_ms < 500 and error_rate == 0, set needs_improvement to false.
"""
