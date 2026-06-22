"""
V7 Improvement Leaderboard

Tracks every improvement (prompt rule, evolved contract rule, validator
addition, pipeline change) and its measured impact on:
  - Success Rate Change (%)
  - Runtime Change (ms)
  - Cost Change ($)
  - Benchmark Impact (score delta)

Only improvements with measured benchmark evidence appear here.
Improvements are ranked by combined impact score.
"""
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

_LEADERBOARD_PATH = Path(__file__).parent.parent.parent / "failure_memory" / "improvement_leaderboard.json"


@dataclass
class ImprovementEntry:
    entry_id: str
    timestamp: str
    improvement_type: str   # prompt_rule | evolved_rule | validator | pipeline | architecture
    description: str
    source: str             # prompt_optimizer | rule_evolution | manual | research_agent
    pattern_key: str        # the failure pattern this fixes (if applicable)

    # Measured impacts
    success_rate_change: float      # e.g. +0.12 = +12pp
    runtime_change_ms: float | None # e.g. -500 = 500ms faster (negative = better)
    cost_change_usd: float | None   # e.g. -0.02 = $0.02 cheaper (negative = better)
    benchmark_score_delta: float    # e.g. +5.3 = 5.3 points higher avg score

    old_pass_rate: float
    new_pass_rate: float
    test_n: int             # how many projects were benchmarked
    combined_impact: float = 0.0    # computed on save


def _compute_combined_impact(entry: ImprovementEntry) -> float:
    """
    Weighted impact score. Higher = more impactful improvement.
    - Success rate change is most important (weight 60%)
    - Benchmark score delta (weight 30%)
    - Cost improvement as a bonus (weight 10%)
    """
    rate_score = entry.success_rate_change * 100 * 0.6
    score_delta = entry.benchmark_score_delta * 0.3
    cost_bonus = 0.0
    if entry.cost_change_usd is not None and entry.cost_change_usd < 0:
        cost_bonus = min(abs(entry.cost_change_usd) * 50, 5.0) * 0.1
    return round(rate_score + score_delta + cost_bonus, 2)


def _load_leaderboard() -> list[dict]:
    if _LEADERBOARD_PATH.exists():
        try:
            return json.loads(_LEADERBOARD_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _save_leaderboard(entries: list[dict]) -> None:
    _LEADERBOARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    _LEADERBOARD_PATH.write_text(
        json.dumps(entries, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def record_improvement(entry: ImprovementEntry) -> None:
    """Add an improvement to the leaderboard."""
    entry.combined_impact = _compute_combined_impact(entry)
    entries = _load_leaderboard()
    entries.append({
        "entry_id": entry.entry_id,
        "timestamp": entry.timestamp,
        "improvement_type": entry.improvement_type,
        "description": entry.description,
        "source": entry.source,
        "pattern_key": entry.pattern_key,
        "success_rate_change": entry.success_rate_change,
        "runtime_change_ms": entry.runtime_change_ms,
        "cost_change_usd": entry.cost_change_usd,
        "benchmark_score_delta": entry.benchmark_score_delta,
        "old_pass_rate": entry.old_pass_rate,
        "new_pass_rate": entry.new_pass_rate,
        "test_n": entry.test_n,
        "combined_impact": entry.combined_impact,
    })
    _save_leaderboard(entries)


def get_leaderboard(top_n: int = 20) -> list[dict]:
    """Return top N improvements sorted by combined impact (highest first)."""
    entries = _load_leaderboard()
    return sorted(entries, key=lambda e: e.get("combined_impact", 0), reverse=True)[:top_n]


def print_leaderboard(top_n: int = 10) -> None:
    entries = get_leaderboard(top_n)
    if not entries:
        print("\n  No improvements recorded yet.")
        return

    print(f"\n{'='*70}")
    print(f"  V7 IMPROVEMENT LEADERBOARD (Top {min(top_n, len(entries))})")
    print(f"{'='*70}")
    print(f"  {'Rank':<5} {'Type':<15} {'Success Rate':>12} {'Score Delta':>12} {'Impact':>8}")
    print(f"  {'-'*5} {'-'*15} {'-'*12} {'-'*12} {'-'*8}")

    for rank, e in enumerate(entries, 1):
        rate = f"{e.get('success_rate_change', 0):+.0%}"
        delta = f"{e.get('benchmark_score_delta', 0):+.1f}pts"
        impact = f"{e.get('combined_impact', 0):.1f}"
        desc = e.get("description", "")[:35]
        type_ = e.get("improvement_type", "")[:13]
        print(f"  #{rank:<4} {type_:<15} {rate:>12} {delta:>12} {impact:>8}")
        print(f"       {desc}")

    print("=" * 70)


def sync_from_prompt_optimizer() -> int:
    """
    Pull accepted improvements from prompt_optimizer_service into the leaderboard.
    Returns number of new entries added.
    """
    from app.services.prompt_optimizer_service import list_accepted_improvements
    existing_ids = {e.get("entry_id") for e in _load_leaderboard()}

    added = 0
    for imp in list_accepted_improvements():
        entry_id = f"po_{imp.get('pattern_key', '')}_{imp.get('timestamp', '')[:10]}"
        if entry_id in existing_ids:
            continue

        entry = ImprovementEntry(
            entry_id=entry_id,
            timestamp=imp.get("timestamp", time.strftime("%Y-%m-%dT%H:%M:%S")),
            improvement_type="prompt_rule",
            description=imp.get("rule", "")[:100],
            source="prompt_optimizer",
            pattern_key=imp.get("pattern_key", ""),
            success_rate_change=float(imp.get("improvement", 0)),
            runtime_change_ms=None,
            cost_change_usd=None,
            benchmark_score_delta=float(imp.get("improvement", 0)) * 100,
            old_pass_rate=float(imp.get("old_pass_rate", 0)),
            new_pass_rate=float(imp.get("new_pass_rate", 0)),
            test_n=3,
        )
        record_improvement(entry)
        added += 1

    return added


def sync_from_rule_evolution() -> int:
    """Pull promoted rules from rule_evolution_service into the leaderboard."""
    from app.services.rule_evolution_service import get_evolution_history
    existing_ids = {e.get("entry_id") for e in _load_leaderboard()}

    added = 0
    for rec in get_evolution_history():
        if not rec.get("promoted"):
            continue
        entry_id = f"re_{rec.get('pattern_key', '')}_{rec.get('timestamp', '')[:10]}"
        if entry_id in existing_ids:
            continue

        entry = ImprovementEntry(
            entry_id=entry_id,
            timestamp=rec.get("timestamp", time.strftime("%Y-%m-%dT%H:%M:%S")),
            improvement_type="evolved_rule",
            description=rec.get("rule", "")[:100],
            source="rule_evolution",
            pattern_key=rec.get("pattern_key", ""),
            success_rate_change=float(rec.get("improvement", 0)),
            runtime_change_ms=None,
            cost_change_usd=None,
            benchmark_score_delta=float(rec.get("improvement", 0)) * 100,
            old_pass_rate=float(rec.get("old_pass_rate", 0)),
            new_pass_rate=float(rec.get("new_pass_rate", 0)),
            test_n=3,
        )
        record_improvement(entry)
        added += 1

    return added
