def build_rule_evolution_prompt(
    failure_patterns: list[dict],
    existing_rules: list[str],
    benchmark_history: list[dict],
) -> str:
    patterns_str = "\n".join(
        f"  [{p['count']}x] {p['key']}: {p.get('examples', [{}])[0].get('error', '')[:150]}"
        for p in failure_patterns[:10]
    )
    existing_str = "\n".join(f"  - {r[:100]}" for r in existing_rules[:10])
    benchmark_str = "\n".join(
        f"  Run {b.get('run_id', '?')}: score={b.get('score', 0)} pass_rate={b.get('pass_rate', 0):.0%}"
        for b in benchmark_history[-5:]
    )

    return f"""You are improving the shared contract rules for an AI code generator.
The generator produces FastAPI + React apps from plain-English ideas.

CURRENT FAILURE PATTERNS (sorted by frequency):
{patterns_str}

EXISTING CONTRACT RULES (already in the prompt):
{existing_str}

RECENT BENCHMARK TREND:
{benchmark_str}

Your task:
1. Identify which failure patterns are NOT yet addressed by existing rules
2. Generate 1-3 new contract rules that would prevent the top unaddressed failures
3. Each rule must be specific, actionable, and testable
4. Do NOT repeat rules already in the existing rules list
5. Prioritize rules that fix the most frequent AND most damaging failures

A good rule:
- States exactly what to do (not just "be careful")
- Includes a WRONG example and RIGHT example
- Is 2-4 sentences
- Under 100 words

Return JSON ONLY (no markdown):
{{
  "new_rules": [
    {{
      "pattern_key": "RouterExportMismatch",
      "rule": "Every route file MUST export a variable named exactly {{resource}}_router (e.g. user_router). WRONG: router = APIRouter(). RIGHT: user_router = APIRouter(). Using `router` as the name causes silent import failures.",
      "confidence": 0.9,
      "expected_impact": "Eliminates RouterExportMismatch failures (17% of runs)"
    }}
  ],
  "rules_to_strengthen": [
    {{
      "existing_rule": "the existing rule text to strengthen",
      "improved_version": "the improved rule text",
      "reason": "why the current version is insufficient"
    }}
  ],
  "analysis": "brief analysis of what patterns are driving failures"
}}"""


def build_rule_validation_prompt(
    candidate_rule: str,
    pattern_key: str,
    examples: list[dict],
) -> str:
    example_str = "\n".join(
        f"  - {e.get('error', '')[:200]}"
        for e in examples[:5]
    )

    return f"""You are validating a candidate rule for an AI code generator.

PATTERN BEING ADDRESSED: {pattern_key}
EXAMPLE ERRORS THIS PATTERN CAUSED:
{example_str}

CANDIDATE RULE:
{candidate_rule}

Evaluate this rule:
1. Would it actually prevent the errors above? (prevention_score: 0-10)
2. Is it clear enough for an LLM to follow? (clarity_score: 0-10)
3. Does it risk causing other problems? (risk_score: 0-10, lower = more risk)
4. Is it short enough to not distract from other rules? (conciseness_score: 0-10)

Return JSON ONLY:
{{
  "prevention_score": 8,
  "clarity_score": 9,
  "risk_score": 8,
  "conciseness_score": 7,
  "overall_quality": 8.0,
  "should_adopt": true,
  "concerns": ["list any concerns"],
  "improved_version": "optional improved version of the rule"
}}"""
