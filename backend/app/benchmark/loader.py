"""
Benchmark prompt loader.

Reads prompt files from benchmarks/<difficulty>/<name>.txt
Returns a flat list of BenchmarkPrompt objects ordered by difficulty then name.

Tiers that include a weights.json file get per-prompt weights; all others default to weight=1.
Weighted tiers: golden (ForgeBench-20), forgebench_realtime, forgebench_ai, forgebench_enterprise.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


BENCHMARKS_DIR = Path(__file__).parent.parent.parent.parent / "benchmarks"

DIFFICULTY_ORDER = [
    "golden",
    "beginner", "intermediate", "advanced", "enterprise", "hard",
    "forgebench_realtime", "forgebench_ai", "forgebench_enterprise",
]

# Tier name → display label
TIER_LABELS = {
    "golden":                "ForgeBench-20",
    "forgebench_realtime":   "ForgeBench-RT",
    "forgebench_ai":         "ForgeBench-AI",
    "forgebench_enterprise": "ForgeBench-ENT",
}


def _load_weights(diff_dir: Path) -> dict[str, float]:
    weights_path = diff_dir / "weights.json"
    if not weights_path.exists():
        return {}
    try:
        data = json.loads(weights_path.read_text(encoding="utf-8"))
        return {k: float(v) for k, v in data.items() if not k.startswith("_")}
    except Exception:
        return {}


@dataclass
class BenchmarkPrompt:
    file:       str        # relative path from benchmarks/, e.g. "beginner/todo.txt"
    name:       str        # e.g. "todo"
    difficulty: str        # "golden" | "beginner" | "intermediate" | ...
    idea:       str        # full prompt text
    weight:     float = 1.0  # importance weight (golden tier uses weights.json)

    def short_name(self) -> str:
        tier = TIER_LABELS.get(self.difficulty, self.difficulty[:3].upper())
        abbr = tier[:3].upper() if len(tier) > 3 else tier.upper()
        return f"[{abbr}] {self.name}"

    def label(self) -> str:
        return TIER_LABELS.get(self.difficulty, self.difficulty)


def load_prompts(
    difficulty: Optional[str] = None,
    name_filter: Optional[str] = None,
    n: Optional[int] = None,
    offset: int = 0,
) -> list[BenchmarkPrompt]:
    """
    Load benchmark prompts from the benchmarks/ directory.

    Args:
        difficulty:  Filter to one difficulty level
        name_filter: Filter by prompt name substring
        n:           Max prompts to return
        offset:      Skip first N prompts

    Returns:
        List of BenchmarkPrompt sorted by difficulty then name.
    """
    prompts: list[BenchmarkPrompt] = []
    dirs = [difficulty] if difficulty else DIFFICULTY_ORDER

    for diff in dirs:
        diff_dir = BENCHMARKS_DIR / diff
        if not diff_dir.exists():
            continue
        weights = _load_weights(diff_dir)
        txt_files = sorted(diff_dir.glob("*.txt"))
        for f in txt_files:
            idea = f.read_text(encoding="utf-8").strip()
            if not idea:
                continue
            if name_filter and name_filter.lower() not in f.stem.lower():
                continue
            prompts.append(BenchmarkPrompt(
                file       = f"{diff}/{f.name}",
                name       = f.stem,
                difficulty = diff,
                idea       = idea,
                weight     = weights.get(f.stem, 1.0),
            ))

    prompts = prompts[offset:]
    if n is not None:
        prompts = prompts[:n]

    return prompts


def list_prompts() -> None:
    """Print all available prompts with weights where applicable."""
    all_prompts = load_prompts()
    for diff in DIFFICULTY_ORDER:
        group = [p for p in all_prompts if p.difficulty == diff]
        if group:
            label = TIER_LABELS.get(diff, diff.upper())
            has_weights = any(p.weight != 1.0 for p in group)
            print(f"\n  {label} ({len(group)})")
            for p in group:
                w = f"  w={p.weight:.0f}" if has_weights else ""
                print(f"    {p.name:<30} {p.idea[:55]}...{w}")
    print(f"\nTotal: {len(all_prompts)} prompts")
