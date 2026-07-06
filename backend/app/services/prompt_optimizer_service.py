"""
V5.3 Self-Improving Prompt System

Watches failure patterns in failure_memory, generates candidate prompt
improvements via LLM, runs a mini-benchmark on N apps, and keeps the
improvement only if it raises the pass rate above a threshold.

Usage:
    from app.services.prompt_optimizer_service import run_prompt_optimization
    result = run_prompt_optimization(provider="cerebras", test_n=5, threshold=0.1)
"""
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

_PROMPT_STORE = Path(__file__).parent.parent.parent / "failure_memory" / "prompt_improvements.json"
_IMPROVEMENT_THRESHOLD = 0.10  # must improve pass rate by at least 10pp to keep


@dataclass
class OptimizationResult:
    improved: bool
    old_pass_rate: float
    new_pass_rate: float
    improvement: float
    rule_added: str
    duration: float
    skipped: bool = False
    skip_reason: str = ""


def _load_improvements() -> list[dict]:
    if _PROMPT_STORE.exists():
        try:
            return json.loads(_PROMPT_STORE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _save_improvements(improvements: list[dict]) -> None:
    _PROMPT_STORE.parent.mkdir(parents=True, exist_ok=True)
    _PROMPT_STORE.write_text(
        json.dumps(improvements, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _get_top_failure(min_count: int = 3) -> dict | None:
    """Return the most frequent failure pattern that doesn't yet have a validated fix."""
    from app.memory.failure_memory import _load, _PATTERN_RULES
    data = _load()
    patterns = data.get("patterns", {})
    improvements = _load_improvements()
    already_addressed = {imp["pattern_key"] for imp in improvements if imp.get("kept")}

    candidates = [
        (key, entry)
        for key, entry in patterns.items()
        if entry["count"] >= min_count and key not in already_addressed
    ]
    if not candidates:
        return None

    key, entry = max(candidates, key=lambda x: x[1]["count"])
    return {"key": key, "count": entry["count"], "examples": entry.get("examples", [])}


def _generate_prompt_rule(pattern: dict, provider: str) -> str | None:
    """Ask the LLM to generate a concrete, actionable rule to prevent this failure."""
    from app.providers.ai_provider import generate_content

    examples_str = "\n".join(
        f"  - {e.get('error', '')[:200]}" for e in pattern["examples"][:3]
    )

    prompt = f"""You are improving an AI code generator's system prompts.

The generator frequently makes this mistake ({pattern['count']} occurrences):
Pattern: {pattern['key']}
Examples of errors caused:
{examples_str}

Write ONE concrete, specific rule (2-4 sentences) to add to the system prompt
that will prevent this mistake. The rule must:
- Be actionable (tell the LLM exactly what to do differently)
- Include a WRONG and RIGHT example
- Be under 100 words

Return ONLY the rule text. No preamble, no explanation."""

    try:
        return generate_content(prompt, provider, max_tokens=300, stage="prompt_optimizer")
    except Exception:
        return None


def _run_mini_benchmark(ideas: list[str], provider: str) -> float:
    """Run N apps and return the perfect-score rate."""
    from app.services.project_service import generate_project
    passed = 0
    for idea in ideas:
        try:
            result = generate_project(idea, provider=provider)
            score = result.get("forge_score", {}).get("score", 0)
            if score == 100:
                passed += 1
        except Exception:
            pass
    return passed / len(ideas) if ideas else 0.0


_MINI_BENCHMARK_IDEAS = [
    "A todo list app with user authentication and task priorities",
    "A blog with posts, comments, and author profiles",
    "A recipe manager with ingredients and categories",
    "A contact book with search and tagging",
    "A expense tracker with categories and monthly summaries",
]


def run_prompt_optimization(
    provider: str = "cerebras",
    test_n: int = 3,
    threshold: float = _IMPROVEMENT_THRESHOLD,
    ideas: list[str] | None = None,
) -> OptimizationResult:
    """
    1. Find the top failure pattern not yet addressed
    2. Generate a candidate rule via LLM
    3. Run mini-benchmark WITHOUT the rule → baseline
    4. Inject rule into shared_contract.py
    5. Run mini-benchmark WITH the rule → new rate
    6. If improvement >= threshold: keep it; else revert
    """
    t0 = time.time()

    pattern = _get_top_failure(min_count=2)
    if not pattern:
        return OptimizationResult(
            improved=False, old_pass_rate=0, new_pass_rate=0,
            improvement=0, rule_added="",
            duration=round(time.time() - t0, 2),
            skipped=True, skip_reason="No failure patterns with enough data yet",
        )

    print(f"\n=== PROMPT OPTIMIZER: targeting {pattern['key']} ({pattern['count']}x) ===")

    # Generate candidate rule
    candidate_rule = _generate_prompt_rule(pattern, provider)
    if not candidate_rule:
        return OptimizationResult(
            improved=False, old_pass_rate=0, new_pass_rate=0,
            improvement=0, rule_added="",
            duration=round(time.time() - t0, 2),
            skipped=True, skip_reason="LLM failed to generate a candidate rule",
        )

    print(f"Candidate rule: {candidate_rule[:150]}...")

    test_ideas = (ideas or _MINI_BENCHMARK_IDEAS)[:test_n]

    # Baseline (without new rule)
    print(f"Running baseline benchmark ({test_n} apps)...")
    old_rate = _run_mini_benchmark(test_ideas, provider)
    print(f"Baseline pass rate: {old_rate:.0%}")

    # Inject rule into shared_contract temporarily
    contract_path = Path(__file__).parent.parent / "prompts" / "shared_contract.py"
    original_content = contract_path.read_text(encoding="utf-8")
    marker = "- NEVER use a bare Python built-in type"
    injection = f"- [AUTO-OPTIMIZED] {candidate_rule.strip()}\n"
    modified_content = original_content.replace(marker, injection + marker)
    contract_path.write_text(modified_content, encoding="utf-8")

    # Reload the module so the change takes effect
    import importlib
    import app.prompts.shared_contract as sc_mod
    importlib.reload(sc_mod)

    # Test with new rule
    print(f"Running test benchmark with new rule ({test_n} apps)...")
    new_rate = _run_mini_benchmark(test_ideas, provider)
    print(f"New pass rate: {new_rate:.0%}  (delta: {new_rate - old_rate:+.0%})")

    improvement = new_rate - old_rate
    kept = improvement >= threshold

    if kept:
        print(f"IMPROVEMENT ACCEPTED (+{improvement:.0%} >= {threshold:.0%} threshold)")
        improvements = _load_improvements()
        improvements.append({
            "pattern_key": pattern["key"],
            "rule": candidate_rule,
            "old_pass_rate": old_rate,
            "new_pass_rate": new_rate,
            "improvement": improvement,
            "kept": True,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        })
        _save_improvements(improvements)
    else:
        print(f"IMPROVEMENT REJECTED ({improvement:+.0%} < {threshold:.0%} threshold) — reverting")
        # Revert contract
        contract_path.write_text(original_content, encoding="utf-8")
        importlib.reload(sc_mod)

    return OptimizationResult(
        improved=kept,
        old_pass_rate=old_rate,
        new_pass_rate=new_rate,
        improvement=improvement,
        rule_added=candidate_rule if kept else "",
        duration=round(time.time() - t0, 2),
    )


def list_accepted_improvements() -> list[dict]:
    return [imp for imp in _load_improvements() if imp.get("kept")]
