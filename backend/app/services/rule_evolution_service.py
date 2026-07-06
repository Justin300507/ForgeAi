"""
V7 Rule Evolution Engine

Automatically discovers new contract rules from failure patterns,
validates them via LLM quality check, benchmarks them against a mini
test suite, and promotes them permanently to shared_contract.py only
when they demonstrably improve pass rate.

Difference from V5's prompt_optimizer_service:
- Generates rules from aggregate pattern analysis, not just the top-1 failure
- Validates rule quality before benchmarking (saves LLM calls)
- Supports strengthening existing rules, not just adding new ones
- Tracks every candidate in the evolution log for research purposes
"""
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

_EVOLUTION_LOG = Path(__file__).parent.parent.parent / "failure_memory" / "rule_evolution_log.json"
_IMPROVEMENT_THRESHOLD = 0.08   # 8pp improvement to auto-promote
_QUALITY_THRESHOLD = 5.5        # minimum quality score to benchmark


@dataclass
class RuleCandidate:
    pattern_key: str
    rule: str
    confidence: float
    expected_impact: str
    quality_score: float = 0.0
    validated: bool = False
    benchmarked: bool = False
    old_pass_rate: float = 0.0
    new_pass_rate: float = 0.0
    improvement: float = 0.0
    promoted: bool = False
    timestamp: str = ""


@dataclass
class EvolutionResult:
    candidates_generated: int
    candidates_validated: int
    candidates_benchmarked: int
    rules_promoted: int
    promoted_rules: list[str] = field(default_factory=list)
    rejected_rules: list[str] = field(default_factory=list)
    duration: float = 0.0
    skipped: bool = False
    skip_reason: str = ""
    analysis: str = ""


def _load_evolution_log() -> list[dict]:
    if _EVOLUTION_LOG.exists():
        try:
            return json.loads(_EVOLUTION_LOG.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _save_evolution_log(log: list[dict]) -> None:
    _EVOLUTION_LOG.parent.mkdir(parents=True, exist_ok=True)
    _EVOLUTION_LOG.write_text(
        json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _get_existing_contract_rules() -> list[str]:
    """Extract existing rules from shared_contract.py."""
    contract_path = Path(__file__).parent.parent / "prompts" / "shared_contract.py"
    if not contract_path.exists():
        return []
    try:
        content = contract_path.read_text(encoding="utf-8")
        rules = []
        for line in content.splitlines():
            line = line.strip()
            if line.startswith("- ") and len(line) > 10:
                rules.append(line[2:])
        return rules
    except Exception:
        return []


def _already_addressed(pattern_key: str) -> bool:
    """Check if this pattern already has a promoted rule."""
    log = _load_evolution_log()
    return any(
        entry.get("pattern_key") == pattern_key and entry.get("promoted")
        for entry in log
    )


def _inject_rule_into_contract(rule: str) -> str:
    """Inject a new rule into shared_contract.py and return original content."""
    contract_path = Path(__file__).parent.parent / "prompts" / "shared_contract.py"
    original = contract_path.read_text(encoding="utf-8")
    marker = "- NEVER use a bare Python built-in type"
    injection = f"- [V7-EVOLVED] {rule.strip()}\n"
    modified = original.replace(marker, injection + marker, 1)
    contract_path.write_text(modified, encoding="utf-8")
    return original


def _revert_contract(original_content: str) -> None:
    contract_path = Path(__file__).parent.parent / "prompts" / "shared_contract.py"
    contract_path.write_text(original_content, encoding="utf-8")


def _reload_contract() -> None:
    import importlib
    try:
        import app.prompts.shared_contract as sc_mod
        importlib.reload(sc_mod)
    except Exception:
        pass


def _validate_rule_quality(candidate: dict, provider: str) -> float:
    """Ask LLM to score the rule quality. Returns overall_quality 0-10."""
    from app.providers.ai_provider import generate_content
    from app.prompts.rule_evolution_prompt import build_rule_validation_prompt
    from app.utils.json_cleaner import extract_json
    from app.memory.failure_memory import _load

    data = _load()
    examples = data.get("patterns", {}).get(candidate["pattern_key"], {}).get("examples", [])

    try:
        prompt = build_rule_validation_prompt(
            candidate_rule=candidate["rule"],
            pattern_key=candidate["pattern_key"],
            examples=examples,
        )
        raw = generate_content(prompt, provider, max_tokens=1000, stage="rule_evolution")
        result = extract_json(raw)
        score = result.get("overall_quality") or result.get("quality_score")
        return float(score) if score is not None else 5.0
    except Exception:
        return 5.0


def _mini_benchmark(ideas: list[str], provider: str) -> float:
    """Run N projects, return success rate (score >= 80 = success)."""
    from app.services.project_service import generate_project
    passed = 0
    for idea in ideas:
        try:
            result = generate_project(idea, provider=provider)
            score = result.get("forge_score", {}).get("score", 0)
            if score >= 80:
                passed += 1
        except Exception:
            pass
    return passed / len(ideas) if ideas else 0.0


_BENCHMARK_IDEAS = [
    "A todo list app with user authentication and task priorities",
    "A blog with posts, comments, and author profiles",
    "A recipe manager with ingredients and categories",
    "A contact book with search and tagging",
    "An expense tracker with categories and monthly summaries",
]


def run_rule_evolution(
    provider: str = "auto",
    test_n: int = 3,
    threshold: float = _IMPROVEMENT_THRESHOLD,
    quality_threshold: float = _QUALITY_THRESHOLD,
    ideas: list[str] | None = None,
) -> EvolutionResult:
    """
    Full rule evolution cycle:
    1. Analyze all failure patterns
    2. Generate candidate rules for unaddressed patterns
    3. Validate each rule for quality
    4. Benchmark high-quality candidates
    5. Promote rules that pass the improvement threshold
    """
    t0 = time.time()

    from app.providers.ai_provider import generate_content
    from app.prompts.rule_evolution_prompt import build_rule_evolution_prompt
    from app.utils.json_cleaner import extract_json
    from app.memory.failure_memory import get_top_patterns, _load

    # Step 1: Get failure patterns and existing rules
    patterns = get_top_patterns(min_count=2, max_patterns=15)
    if not patterns:
        return EvolutionResult(
            candidates_generated=0, candidates_validated=0,
            candidates_benchmarked=0, rules_promoted=0,
            duration=round(time.time() - t0, 2),
            skipped=True, skip_reason="No failure patterns with enough data",
        )

    existing_rules = _get_existing_contract_rules()
    evolution_log = _load_evolution_log()

    # Load benchmark history (from cost log if available)
    benchmark_history = []
    cost_log = Path(__file__).parent.parent.parent / "cost_log.jsonl"
    if cost_log.exists():
        try:
            for line in cost_log.read_text().splitlines()[-20:]:
                if line.strip():
                    benchmark_history.append(json.loads(line))
        except Exception:
            pass

    # Step 2: Generate candidate rules
    print(f"\n=== V7 RULE EVOLUTION ===")
    print(f"  Analyzing {len(patterns)} failure patterns...")

    try:
        prompt = build_rule_evolution_prompt(
            failure_patterns=patterns,
            existing_rules=existing_rules,
            benchmark_history=benchmark_history,
        )
        raw = generate_content(prompt, provider, max_tokens=2000, stage="rule_evolution")
        evolution_data = extract_json(raw)
    except Exception as e:
        return EvolutionResult(
            candidates_generated=0, candidates_validated=0,
            candidates_benchmarked=0, rules_promoted=0,
            duration=round(time.time() - t0, 2),
            skipped=True, skip_reason=f"LLM call failed: {e}",
        )

    new_rules = evolution_data.get("new_rules", [])
    analysis = evolution_data.get("analysis", "")
    _safe = lambda s, n=150: str(s)[:n].encode("ascii", errors="replace").decode("ascii")
    print(f"  Generated {len(new_rules)} candidate rules")
    print(f"  Analysis: {_safe(analysis)}")

    # Step 3: Filter out patterns already addressed
    candidates = [
        RuleCandidate(
            pattern_key=r.get("pattern_key", "unknown"),
            rule=r.get("rule", ""),
            confidence=float(r.get("confidence", 0.5)),
            expected_impact=r.get("expected_impact", ""),
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
        )
        for r in new_rules
        if r.get("rule") and not _already_addressed(r.get("pattern_key", ""))
    ]

    if not candidates:
        return EvolutionResult(
            candidates_generated=len(new_rules),
            candidates_validated=0, candidates_benchmarked=0, rules_promoted=0,
            duration=round(time.time() - t0, 2),
            skipped=True, skip_reason="All patterns already have promoted rules",
            analysis=analysis,
        )

    # Step 4: Validate quality of each candidate
    validated = []
    for c in candidates:
        score = _validate_rule_quality({"pattern_key": c.pattern_key, "rule": c.rule}, provider)
        c.quality_score = score
        c.validated = True
        rule_preview = c.rule[:60].encode("ascii", errors="replace").decode("ascii")
        print(f"  Quality [{c.pattern_key}]: {score:.1f}/10 -- {rule_preview}...")
        if score >= quality_threshold:
            validated.append(c)

    if not validated:
        return EvolutionResult(
            candidates_generated=len(new_rules),
            candidates_validated=len(candidates),
            candidates_benchmarked=0, rules_promoted=0,
            duration=round(time.time() - t0, 2),
            skipped=True,
            skip_reason=f"No candidates passed quality threshold ({quality_threshold})",
            analysis=analysis,
        )

    # Step 5: Benchmark each high-quality candidate
    test_ideas = (ideas or _BENCHMARK_IDEAS)[:test_n]
    promoted = []
    rejected = []
    benchmarked_count = 0

    print(f"  Benchmarking {len(validated)} candidates ({test_n} apps each)...")
    print(f"  Getting baseline pass rate...")
    baseline_rate = _mini_benchmark(test_ideas, provider)
    print(f"  Baseline: {baseline_rate:.0%}")

    for c in sorted(validated, key=lambda x: x.confidence, reverse=True)[:3]:
        original = _inject_rule_into_contract(c.rule)
        _reload_contract()

        try:
            new_rate = _mini_benchmark(test_ideas, provider)
            c.benchmarked = True
            c.old_pass_rate = baseline_rate
            c.new_pass_rate = new_rate
            c.improvement = new_rate - baseline_rate
            benchmarked_count += 1

            print(f"  [{c.pattern_key}] {baseline_rate:.0%} -> {new_rate:.0%} (delta {c.improvement:+.0%})")

            if c.improvement >= threshold:
                c.promoted = True
                promoted.append(c.rule)
                rule_preview = c.rule[:80].encode("ascii", errors="replace").decode("ascii")
                print(f"  PROMOTED: {rule_preview}...")
                evolution_log.append({
                    "pattern_key": c.pattern_key,
                    "rule": c.rule,
                    "quality_score": c.quality_score,
                    "old_pass_rate": c.old_pass_rate,
                    "new_pass_rate": c.new_pass_rate,
                    "improvement": c.improvement,
                    "promoted": True,
                    "timestamp": c.timestamp,
                })
            else:
                rejected.append(c.rule)
                _revert_contract(original)
                _reload_contract()
                evolution_log.append({
                    "pattern_key": c.pattern_key,
                    "rule": c.rule,
                    "quality_score": c.quality_score,
                    "old_pass_rate": c.old_pass_rate,
                    "new_pass_rate": c.new_pass_rate,
                    "improvement": c.improvement,
                    "promoted": False,
                    "timestamp": c.timestamp,
                })
        except Exception as e:
            _revert_contract(original)
            _reload_contract()
            print(f"  Benchmark error for {c.pattern_key}: {e}")

    _save_evolution_log(evolution_log)

    return EvolutionResult(
        candidates_generated=len(new_rules),
        candidates_validated=len(candidates),
        candidates_benchmarked=benchmarked_count,
        rules_promoted=len(promoted),
        promoted_rules=promoted,
        rejected_rules=rejected,
        duration=round(time.time() - t0, 2),
        analysis=analysis,
    )


def get_evolution_history() -> list[dict]:
    """Return all candidates (promoted and rejected) from evolution history."""
    return _load_evolution_log()
