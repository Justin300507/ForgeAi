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

# Cost per 1M tokens in USD — input and output tracked separately where known.
# Gemini 2.5 Flash: $0.15/1M input, $0.60/1M output (thinking disabled, was $3.50/1M thinking)
# Groq free tier: $0.00 (daily rate-limited, not billed)
_COST_PER_1M_INPUT = {
    "gemini": 0.15,
    "cerebras": 0.60,
    "groq": 0.00,
    "openrouter": 1.00,
    "ollama": 0.00,
    "auto": 0.60,
}
_COST_PER_1M_OUTPUT = {
    "gemini": 0.60,
    "cerebras": 0.60,
    "groq": 0.00,
    "openrouter": 1.00,
    "ollama": 0.00,
    "auto": 0.60,
}

_session_calls: list[dict] = []  # in-memory log for the current run

# Run-level cumulative totals. Unlike _session_calls, these survive
# flush_to_log() — V6 flushes (and clears) the session log at ITS end, which
# zeroed out V15's final report even though 100k+ tokens were spent. Cleared
# only by reset_session(), which V15 calls at the start of each run.
_run_totals = {
    "calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
    "total_tokens": 0, "cost_usd": 0.0,
    "cache_hits": 0, "cache_saved_tokens": 0, "cache_saved_usd": 0.0,
}

# Cache hits fire in ai_provider.generate_content() BEFORE a provider is ever
# chosen, so we don't know which provider's rate would have applied. "auto"
# (Gemini's rate, the priciest live provider) gives a conservative — i.e.
# not-overstated — estimate of savings rather than guessing Groq's $0.
_CACHE_HIT_RATE_BASIS = "auto"


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
    prov = provider.lower()
    in_rate = _COST_PER_1M_INPUT.get(prov, 1.00)
    out_rate = _COST_PER_1M_OUTPUT.get(prov, 1.00)
    estimated_cost = (prompt_tokens / 1_000_000) * in_rate + (completion_tokens / 1_000_000) * out_rate

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
    _run_totals["calls"] += 1
    _run_totals["prompt_tokens"] += prompt_tokens
    _run_totals["completion_tokens"] += completion_tokens
    _run_totals["total_tokens"] += total_tokens
    _run_totals["cost_usd"] += estimated_cost


def record_cache_hit(stage: str, prompt_tokens: int, completion_tokens: int) -> None:
    """Call this when generate_content() serves a cached response instead of
    billing a provider — makes the LLM cache's savings visible in the cost
    report instead of the hit silently vanishing with 0 recorded tokens."""
    saved_tokens = prompt_tokens + completion_tokens
    in_rate = _COST_PER_1M_INPUT.get(_CACHE_HIT_RATE_BASIS, 1.00)
    out_rate = _COST_PER_1M_OUTPUT.get(_CACHE_HIT_RATE_BASIS, 1.00)
    saved_cost = (prompt_tokens / 1_000_000) * in_rate + (completion_tokens / 1_000_000) * out_rate
    _run_totals["cache_hits"] += 1
    _run_totals["cache_saved_tokens"] += saved_tokens
    _run_totals["cache_saved_usd"] += saved_cost


def get_run_totals() -> dict:
    """Cumulative totals for the whole run — unaffected by flush_to_log()."""
    return dict(_run_totals)


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
    """Clear in-memory call log AND run totals for a new generation."""
    _session_calls.clear()
    for k in _run_totals:
        _run_totals[k] = 0.0 if k.endswith("_usd") else 0


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
    hits = _run_totals["cache_hits"]
    if hits:
        print(f"  Cache hits:   {hits} calls served free "
              f"(~${_run_totals['cache_saved_usd']:.4f}, "
              f"~{_run_totals['cache_saved_tokens']:,} tokens saved)")


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
