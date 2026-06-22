def build_research_agent_prompt(
    benchmark_history: list[dict],
    failure_patterns: list[dict],
    accepted_improvements: list[dict],
    v_comparison: dict,
) -> str:
    history_str = "\n".join(
        f"  {b.get('date', '?')[:10]} | score={b.get('score', 0)} | "
        f"pass_rate={b.get('pass_rate', 0):.0%} | runtime_rate={b.get('runtime_rate', 0):.0%} | "
        f"cost=${b.get('cost_usd', 0):.3f} | version={b.get('version', 'v5')}"
        for b in benchmark_history[-20:]
    )
    pattern_str = "\n".join(
        f"  [{p['count']}x] {p['key']}: {p.get('rule', '')[:100]}"
        for p in failure_patterns[:10]
    )
    improvement_str = "\n".join(
        f"  [{imp.get('timestamp', '')[:10]}] {imp.get('pattern_key', '')}: "
        f"+{imp.get('improvement', 0):.0%} pass rate"
        for imp in accepted_improvements[-10:]
    )
    v_str = "\n".join(
        f"  {version}: score={data.get('avg_score', 0):.1f} | pass_rate={data.get('pass_rate', 0):.0%}"
        for version, data in v_comparison.items()
    )

    return f"""You are an autonomous research agent for ForgeAI — an AI code generator.
Your job is to analyze benchmark trends, discover failure patterns, and suggest
concrete improvements to increase the system's project success rate.

BENCHMARK HISTORY (recent 20 runs):
{history_str}

TOP FAILURE PATTERNS:
{pattern_str}

ACCEPTED IMPROVEMENTS (already applied):
{improvement_str}

VERSION COMPARISON:
{v_str}

Perform a deep analysis:

1. TREND ANALYSIS: Are scores improving or regressing? What changed recently?
2. FAILURE ROOT CAUSES: What are the underlying causes (not symptoms) of failures?
3. REGRESSION DETECTION: Did any recent improvement cause a regression elsewhere?
4. COST EFFICIENCY: Is the cost per successful project improving?
5. PATTERN CLUSTERS: Which failures tend to co-occur? What does that reveal?
6. IMPROVEMENT OPPORTUNITIES: What 3 specific changes would most improve success rate?
7. HYPOTHESIS: Form a testable hypothesis about what change would improve score by 10+ points

Return JSON ONLY (no markdown):
{{
  "trend": "improving|stable|declining",
  "trend_explanation": "...",
  "regression_detected": false,
  "regression_details": "",
  "root_causes": [
    {{
      "cause": "LLM doesn't consistently follow router naming convention",
      "evidence": "RouterExportMismatch appears in 23% of runs",
      "fix_type": "prompt_rule|architecture_change|validator_addition"
    }}
  ],
  "cost_efficiency_trend": "improving|stable|declining",
  "pattern_clusters": [
    {{
      "cluster_name": "Import failures",
      "patterns": ["ModuleNotFoundError", "MissingRouteFile"],
      "frequency": 0.34,
      "insight": "These co-occur because main.py imports routes before they're created"
    }}
  ],
  "top_improvements": [
    {{
      "rank": 1,
      "improvement": "Add validator that checks all main.py imports exist before generation ends",
      "expected_impact": "+15% pass rate",
      "effort": "low|medium|high",
      "type": "validator|prompt_rule|architecture|pipeline"
    }}
  ],
  "hypothesis": {{
    "statement": "Adding explicit missing-file detection before the fix loop will reduce ModuleNotFoundError by 50%",
    "test_method": "Run 10 benchmark projects with and without the change",
    "expected_delta": "+8% pass rate"
  }},
  "summary": "3-4 sentence research summary"
}}"""
