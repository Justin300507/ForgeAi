"""
V5.7 Cost Tracking

Tracks tokens, estimated cost, runtime, fix attempts, and model used
per generation run. Persists to failure_memory/cost_log.json.
"""
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

_COST_LOG = Path(__file__).parent.parent.parent / "failure_memory" / "cost_log.json"

# Approximate cost per 1M tokens (input/output averaged) in USD
_COST_PER_1M_TOKENS = {
    "cerebras": 0.60,
    "groq": 0.10,
    "gemini": 0.35,
    "openrouter": 1.00,
    "ollama": 0.00,
    "auto": 0.60,
}

_session_calls: list[dict] = []  # in-memory log for the current run


def record_llm_call(
    stage: str,
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    duration_s: float,
):
    """Call this after every LLM API call to track usage."""
    total_tokens = prompt_tokens + completion_tokens
    cost_per_1m = _COST_PER_1M_TOKENS.get(provider.lower(), 1.00)
    estimated_cost = (total_tokens / 1_000_000) * cost_per_1m

    _session_calls.append({
        "stage": stage,
        "provider": provider,
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "estimated_cost_usd": round(estimated_cost, 6),
        "duration_s": round(duration_s, 2),
    })


def get_session_summary() -> dict:
    """Return aggregated stats for all calls in the current session."""
    if not _session_calls:
        return {
            "total_calls": 0,
            "total_tokens": 0,
            "total_cost_usd": 0.0,
            "total_llm_time_s": 0.0,
            "by_stage": {},
        }

    by_stage: dict[str, dict] = {}
    for call in _session_calls:
        stage = call["stage"]
        if stage not in by_stage:
            by_stage[stage] = {"calls": 0, "tokens": 0, "cost_usd": 0.0, "time_s": 0.0}
        by_stage[stage]["calls"] += 1
        by_stage[stage]["tokens"] += call["total_tokens"]
        by_stage[stage]["cost_usd"] += call["estimated_cost_usd"]
        by_stage[stage]["time_s"] += call["duration_s"]

    return {
        "total_calls": len(_session_calls),
        "total_tokens": sum(c["total_tokens"] for c in _session_calls),
        "total_cost_usd": round(sum(c["estimated_cost_usd"] for c in _session_calls), 6),
        "total_llm_time_s": round(sum(c["duration_s"] for c in _session_calls), 2),
        "by_stage": by_stage,
    }


def flush_to_log(project_name: str, forge_score: int, total_wall_time_s: float) -> None:
    """Persist session stats to cost_log.json after a generation completes."""
    summary = get_session_summary()
    summary["project_name"] = project_name
    summary["forge_score"] = forge_score
    summary["wall_time_s"] = round(total_wall_time_s, 2)
    summary["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S")

    _COST_LOG.parent.mkdir(parents=True, exist_ok=True)
    data = []
    if _COST_LOG.exists():
        try:
            data = json.loads(_COST_LOG.read_text(encoding="utf-8"))
        except Exception:
            pass
    data.append(summary)
    _COST_LOG.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    _session_calls.clear()


def reset_session() -> None:
    """Clear in-memory call log for a new generation."""
    _session_calls.clear()


def print_session_cost():
    """Print cost summary to stdout."""
    s = get_session_summary()
    print(f"\n=== COST SUMMARY ===")
    print(f"  LLM calls:    {s['total_calls']}")
    print(f"  Total tokens: {s['total_tokens']:,}")
    print(f"  Est. cost:    ${s['total_cost_usd']:.4f}")
    print(f"  LLM time:     {s['total_llm_time_s']:.1f}s")
    for stage, stats in s.get("by_stage", {}).items():
        print(f"    {stage}: {stats['tokens']:,} tokens  ${stats['cost_usd']:.4f}")


def load_cost_history() -> list[dict]:
    if _COST_LOG.exists():
        try:
            return json.loads(_COST_LOG.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def print_cost_report(last_n: int = 10) -> None:
    history = load_cost_history()[-last_n:]
    if not history:
        print("No cost history yet.")
        return
    print(f"\n=== COST REPORT (last {len(history)} runs) ===")
    total_cost = sum(r.get("total_cost_usd", 0) for r in history)
    total_tokens = sum(r.get("total_tokens", 0) for r in history)
    print(f"  Total cost:   ${total_cost:.4f}")
    print(f"  Total tokens: {total_tokens:,}")
    print(f"  Avg per run:  ${total_cost/len(history):.4f}  {total_tokens//len(history):,} tokens")
    for r in history[-5:]:
        print(f"  [{r.get('timestamp','?')[:16]}] {r.get('project_name','?'):20s}  "
              f"score={r.get('forge_score','?'):3}  "
              f"${r.get('total_cost_usd',0):.4f}  "
              f"{r.get('total_tokens',0):,}tok  "
              f"{r.get('wall_time_s',0):.0f}s")
